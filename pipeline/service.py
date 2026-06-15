"""Service de prédiction : la fonction pure du contrat.

`predict(competition, home, away, neutral)` renvoie le dictionnaire complet du
contrat de sortie (1/X/2 calibré, buts attendus, score le plus probable, matrice
des scores, over/under 2.5, BTTS). C'est le cœur réutilisé par l'API (Phase 4).

Le service charge paresseusement, depuis data/models/, les artefacts entraînés
(Dixon-Coles + XGBoost + calibration) et, depuis la base, l'état courant des équipes
(forme, Elo, xG) pour fabriquer le vecteur de features du match demandé.
"""

from __future__ import annotations

import copy
import functools
import math

import pandas as pd

from datetime import datetime, timedelta, timezone

from . import badges, config, db, devig, features, journal
from . import names as names_mod
from . import odds_api, scorers, staking
from .qualitative import QualitativeLayer
from .train import load_predictor

# Compétitions de clubs reconnues (le reste est traité comme sélections).
_CLUB_COMPETITIONS = {lg["competition"] for lg in config.CLUB_LEAGUES}

_qualitative = QualitativeLayer()


def domain_of(competition: str) -> str:
    return config.DOMAIN_CLUB if competition in _CLUB_COMPETITIONS else config.DOMAIN_INTL


def _host_log_bonus(competition: str, home_name: str,
                    away_name: str) -> tuple[float, float, str | None]:
    """Bonus FIXE de pays hôte en Coupe du Monde, en log-buts.

    Renvoie (bonus_domicile, bonus_extérieur, nom_de_l_hôte). Valeur a priori
    (jamais ajustée sur les résultats -> aucune fuite). Si les deux équipes sont
    co-hôtes, aucune des deux n'a d'avantage différentiel -> (0, 0, None).
    """
    if competition != config.WORLD_CUP_COMPETITION:
        return 0.0, 0.0, None
    b = config.HOST_GOAL_LOG_BONUS
    home_host = home_name in config.WORLD_CUP_HOSTS
    away_host = away_name in config.WORLD_CUP_HOSTS
    if home_host and not away_host:
        return b, 0.0, home_name
    if away_host and not home_host:
        return 0.0, b, away_name
    return 0.0, 0.0, None


# Cache des prédictions statistiques identiques (même match, mêmes options).
# Déterministe tant que les modèles/snapshots ne changent pas : vidé par
# `clear_caches()` après un refresh / ré-entraînement. On ne met EN cache que le
# socle statistique (couche qualitative OFF) ; la couche qualitative, sensible au
# temps et au réseau, garde son propre cache à TTL court.
_PRED_CACHE: dict = {}
_PRED_CACHE_MAX = 256


@functools.lru_cache(maxsize=4)
def _predictor(domain: str):
    return load_predictor(domain)


@functools.lru_cache(maxsize=4)
def _snapshot(domain: str) -> dict:
    conn = db.connect()
    snap = features.team_state_snapshot(conn, domain)
    conn.close()
    return snap


@functools.lru_cache(maxsize=4)
def _team_names(domain: str) -> dict:
    """team_id -> nom canonique affichable."""
    conn = db.connect()
    df = pd.read_sql_query(
        "SELECT team_id, canonical_name FROM teams WHERE domain = ?",
        conn, params=(domain,))
    conn.close()
    return dict(zip(df["team_id"], df["canonical_name"]))


def _resolve(domain: str, name: str) -> str | None:
    """Accepte un team_id ou un nom canonique, renvoie le team_id."""
    names = _team_names(domain)
    if name in names:           # déjà un team_id
        return name
    inv = {v: k for k, v in names.items()}
    return inv.get(name)


def _resolve_fuzzy(domain: str, name: str, min_score: float = 0.6) -> str | None:
    """Résout un libellé (souvent issu d'un bookmaker) vers un team_id.

    Essaie d'abord la résolution exacte, puis les alias the-odds-api, puis un
    appariement flou sur les noms canoniques. None si rien d'assez fiable
    (on préfère ignorer un match plutôt que d'afficher une mauvaise équipe).
    """
    if not name:
        return None
    exact = _resolve(domain, name)
    if exact:
        return exact
    # alias connus (USA→United States, Czechia→Czech Republic, Man City…) : on tente
    # d'abord une résolution exacte sur le nom canonique résolu.
    resolved = names_mod.resolve_alias(name)
    if resolved != name:
        exact = _resolve(domain, resolved)
        if exact:
            return exact
    # appariement flou, tolérant aux alias des DEUX côtés (clé d'alias identique → 1.0)
    nk = names_mod.alias_key(name)
    best_id, best_score = None, 0.0
    for tid, nm in _team_names(domain).items():
        s = 1.0 if names_mod.alias_key(nm) == nk else names_mod._similarity(resolved, nm)
        if s > best_score:
            best_id, best_score = tid, s
    return best_id if best_score >= min_score else None


def list_teams(domain: str | None = None) -> list[dict]:
    domains = [domain] if domain else [config.DOMAIN_CLUB, config.DOMAIN_INTL]
    out = []
    for d in domains:
        names = _team_names(d)
        snap = _snapshot(d)
        for tid, nm in sorted(names.items(), key=lambda kv: kv[1]):
            if tid in snap:      # uniquement les équipes avec un historique
                out.append({"id": tid, "name": nm, "domain": d,
                            "elo": round(snap[tid]["elo"], 1),
                            "badge": badges.badge(nm, d)})
    return out


def _num(x):
    """Float exploitable, ou None (filtre NaN/None) pour les explications."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _h2h_record(domain: str, hid: str, aid: str, limit: int = 8) -> dict | None:
    """Bilan des dernières confrontations directes entre deux équipes.

    Lecture seule de la base (matchs passés uniquement, aucune fuite : on n'utilise
    que des résultats déjà joués pour *expliquer* la prédiction). Renvoie le bilan
    du point de vue de `hid` (domicile demandé), ou None si aucune confrontation.
    """
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT home_team_id, away_team_id, home_goals, away_goals
               FROM matches
               WHERE ((home_team_id=? AND away_team_id=?)
                   OR (home_team_id=? AND away_team_id=?))
                 AND home_goals IS NOT NULL AND away_goals IS NOT NULL
               ORDER BY date DESC LIMIT ?""",
            (hid, aid, aid, hid, limit)).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    hw = d = aw = 0
    for h, _a, hg, ag in rows:
        gd = (hg - ag) if h == hid else (ag - hg)   # >0 si l'équipe `hid` gagne
        if gd > 0:
            hw += 1
        elif gd < 0:
            aw += 1
        else:
            d += 1
    return {"n": len(rows), "home_wins": hw, "draws": d, "away_wins": aw}


def _explain(feat: dict, home: str, away: str, neutral: bool,
             h2h: dict | None = None, host: str | None = None) -> dict:
    """Explication factuelle des facteurs dominants, à partir des SEULES features
    déjà utilisées par le modèle (pré-match, sans aucune fuite).

    Renvoie une synthèse en une phrase + quelques facteurs en langage simple. On
    traduit ce que le modèle voit déjà (Elo, forme, terrain) et on ajoute deux
    repères factuels parlants : le bilan des confrontations directes (`h2h`) et un
    éventuel écart de fraîcheur (jours de repos).
    """
    factors: list[str] = []

    # 1. Force globale (Elo) — le moteur principal du modèle.
    diff = _num(feat.get("elo_diff"))
    if diff is not None:
        ad = round(abs(diff))
        if ad < 20:
            factors.append(f"Forces très proches au classement Elo (écart de {ad} pts).")
        else:
            strong = home if diff > 0 else away
            level = ("nettement" if ad >= 150 else
                     "sensiblement" if ad >= 60 else "légèrement")
            factors.append(f"Au classement Elo, {strong} est {level} devant "
                           f"(écart de {ad} pts).")

    # 2. Forme récente (5 derniers matchs, en points par match).
    hppg, appg = _num(feat.get("home_form5_ppg")), _num(feat.get("away_form5_ppg"))
    if hppg is not None and appg is not None:
        if abs(hppg - appg) < 0.3:
            factors.append(f"Forme récente comparable ({hppg:.1f} contre {appg:.1f} "
                           f"pt/match sur 5 matchs).")
        else:
            inform = home if hppg > appg else away
            factors.append(f"Meilleure forme récente pour {inform} "
                           f"({max(hppg, appg):.1f} contre {min(hppg, appg):.1f} "
                           f"pt/match sur 5 matchs).")

    # 3. Avantage du terrain.
    if neutral:
        factors.append("Terrain neutre : pas d'avantage du domicile.")
    elif _num(feat.get("home_advantage")):
        factors.append(f"{home} profite de l'avantage de jouer à domicile.")

    # 3 bis. Pays hôte de la Coupe du Monde : avantage réel même sur terrain neutre.
    if host:
        factors.append(f"{host} joue la Coupe du Monde à domicile (pays hôte) : "
                       f"un avantage marqué, en plus du reste.")

    # 4. Confrontations directes (historique réel des face-à-face).
    if h2h and h2h.get("n"):
        n, hw, dr, aw = h2h["n"], h2h["home_wins"], h2h["draws"], h2h["away_wins"]
        if hw == aw:
            factors.append(f"Confrontations directes équilibrées sur les {n} derniers "
                           f"face-à-face ({hw} victoire(s) chacun, {dr} nul(s)).")
        else:
            lead, lead_w, opp_w = (home, hw, aw) if hw > aw else (away, aw, hw)
            factors.append(f"Sur les {n} dernières confrontations directes, {lead} mène "
                           f"({lead_w} victoire(s) à {opp_w}, {dr} nul(s)).")

    # 5. Fraîcheur : écart de repos NOTABLE et plausible (hors longue coupure).
    rh, ra = _num(feat.get("home_rest_days")), _num(feat.get("away_rest_days"))
    if rh is not None and ra is not None and max(rh, ra) <= 21 and abs(rh - ra) >= 2:
        fresher = home if rh > ra else away
        factors.append(f"{fresher} a eu plus de repos avant le match "
                       f"({int(max(rh, ra))} j contre {int(min(rh, ra))} j).")

    # Synthèse : qui le modèle voit devant, et pourquoi en premier lieu.
    if diff is None or abs(diff) < 20:
        summary = "Sur le papier, les deux équipes sont au coude-à-coude."
    else:
        strong = home if diff > 0 else away
        summary = f"Le modèle penche pour {strong}, d'abord sur la force globale (Elo)."

    return {"summary": summary, "factors": factors}


def predict(competition: str, home: str, away: str, neutral: bool = False,
            use_qualitative: bool | None = None) -> dict:
    domain = domain_of(competition)
    is_club = domain == config.DOMAIN_CLUB
    snap = _snapshot(domain)

    hid = _resolve(domain, home)
    aid = _resolve(domain, away)
    if hid is None or aid is None:
        raise ValueError(f"Équipe inconnue : {home if hid is None else away}")
    if hid not in snap or aid not in snap:
        raise ValueError("Équipe sans historique exploitable.")

    # État effectif de la couche qualitative pour cette requête.
    effective = (_qualitative.enabled if use_qualitative is None
                 else bool(use_qualitative))
    # Cache process : on sert une copie pour isoler l'appelant (évite qu'une
    # mutation aval ne corrompe l'entrée). Uniquement pour le socle statistique.
    # La compétition fait partie de la clé : elle change les features (avantage
    # terrain spécifique) ET l'effet pays hôte en Coupe du Monde.
    cache_key = (domain, competition, hid, aid, bool(neutral)) if not effective else None
    if cache_key is not None and cache_key in _PRED_CACHE:
        return copy.deepcopy(_PRED_CACHE[cache_key])

    feat = features.make_match_features(
        snap[hid], snap[aid], neutral, competition, is_club)

    names = _team_names(domain)
    home_name, away_name = names.get(hid, hid), names.get(aid, aid)

    # Couche qualitative optionnelle (off par défaut). `use_qualitative` permet de
    # l'activer/désactiver par requête depuis l'UI, sans toucher au .env.
    adjustment = _qualitative.adjust(home_name, away_name, competition,
                                     enabled_override=use_qualitative)

    # Effet pays hôte (Coupe du Monde, fixe, sans fuite) : bonus de buts pour
    # l'hôte, même sur terrain neutre.
    bh, ba, host = _host_log_bonus(competition, home_name, away_name)

    pred = _predictor(domain).predict(hid, aid, neutral=neutral, feat=feat,
                                      adjustment=adjustment,
                                      host_log_bonus=(bh, ba))
    pred["home"] = home_name
    pred["away"] = away_name
    pred["home_badge"] = badges.badge(home_name, domain)
    pred["away_badge"] = badges.badge(away_name, domain)
    pred["competition"] = competition
    pred["domain"] = domain
    pred["why"] = _explain(feat, home_name, away_name, neutral,
                           h2h=_h2h_record(domain, hid, aid), host=host)
    if host:
        pred["host_effect"] = {
            "team": host,
            "goal_boost_pct": round((math.exp(config.HOST_GOAL_LOG_BONUS) - 1) * 100),
        }
    # État EFFECTIF de la couche pour cette requête (l'UI affiche le bon panneau).
    pred["qualitative_enabled"] = effective

    # Journal des prédictions (apprentissage) : enregistré AVANT que le résultat soit
    # connu, uniquement pour un vrai match à venir. Best-effort, ne casse rien.
    journal.maybe_log(pred, domain, competition, hid, aid, neutral, effective)

    if cache_key is not None:
        if len(_PRED_CACHE) >= _PRED_CACHE_MAX:
            _PRED_CACHE.clear()         # purge simple : borne la mémoire
        _PRED_CACHE[cache_key] = copy.deepcopy(pred)
    return pred


_SELECTION_LABELS = {
    "home": "Victoire {home}", "draw": "Match nul", "away": "Victoire {away}",
    "over25": "Plus de 2,5 buts", "under25": "Moins de 2,5 buts",
    "btts": "Les deux marquent", "btts_no": "Pas les deux marquent",
}


def _selection_prob(pred: dict, selection: str) -> float | None:
    """Probabilité du modèle pour l'issue demandée (None si inconnue)."""
    over = pred.get("p_over_2_5")
    btts = pred.get("p_btts")
    return {
        "home": pred.get("p_home_win"), "draw": pred.get("p_draw"),
        "away": pred.get("p_away_win"),
        "over25": over, "under25": None if over is None else 1.0 - over,
        "btts": btts, "btts_no": None if btts is None else 1.0 - btts,
    }.get(selection)


def recommend_stake(competition: str, home: str, away: str, selection: str,
                    odds: float, bankroll: float = 100.0, neutral: bool = False,
                    use_qualitative: bool | None = None) -> dict:
    """Recommandation de mise (Phase P3) pour une issue + un prix saisi par l'utilisateur.

    On calcule la probabilité du modèle pour l'issue, puis l'edge, l'espérance et la
    mise conseillée en Kelly fractionné plafonné. Aucune incitation s'il n'y a pas de value.
    """
    selection = (selection or "").strip().lower()
    if selection not in _SELECTION_LABELS:
        raise ValueError(f"Issue inconnue : {selection!r}")

    pred = predict(competition, home, away, neutral=neutral, use_qualitative=use_qualitative)
    prob = _selection_prob(pred, selection)
    if prob is None:
        raise ValueError(f"Issue indisponible pour ce match : {selection!r}")

    rec = staking.recommend(float(prob), float(odds), float(bankroll))
    label = _SELECTION_LABELS[selection].format(
        home=pred.get("home", home), away=pred.get("away", away))
    rec.update({
        "competition": competition, "domain": pred.get("domain"),
        "home": pred.get("home", home), "away": pred.get("away", away),
        "selection": selection, "selection_label": label,
    })
    # Garde-fou anti fausse-value : si la value tient sur un outsider où le modèle est
    # en fort désaccord avec le prix, on le SIGNALE (proba 1/cote utilisée comme proxy
    # du marché, prudente car elle inclut la marge). On ne retire pas la value, on prévient.
    if rec.get("value"):
        reliability, reason = _value_reliability(
            pred.get("domain"), float(prob), rec.get("market_implied_prob"))
        if reliability == "low":
            rec["reliability"] = "low"
            rec["warnings"] = [reason] + rec.get("warnings", [])
    return rec


# ---------------------------------------------------------------------------
# Phase P4 : agrégation de cotes multi-bookmakers en live (line shopping).
# ---------------------------------------------------------------------------
def _model_probs_by_selection(pred: dict) -> dict:
    """Proba du modèle pour chaque issue gérée par le comparateur de cotes."""
    over = pred.get("p_over_2_5")
    return {
        "home": pred.get("p_home_win"), "draw": pred.get("p_draw"),
        "away": pred.get("p_away_win"),
        "over": over, "under": None if over is None else 1.0 - over,
    }


_LIVE_SELECTION_LABELS = {
    "home": "Victoire {home}", "draw": "Match nul", "away": "Victoire {away}",
    "over": "Plus de 2,5 buts", "under": "Moins de 2,5 buts",
}


def _value_reliability(domain: str, model_prob: float | None,
                       market_fair: float | None) -> tuple[str, str | None]:
    """Fiabilité d'une value : « ok » ou « low » (+ raison) — garde-fou anti fausse-value.

    Une value est jugée PEU FIABLE quand le marché voit l'issue en OUTSIDER et que le
    modèle est en FORT désaccord (proba modèle ≥ ratio × proba équitable marché) :
    c'est là que le modèle se trompe le plus, surtout en sélections (calibration vs
    marché non prouvée). On ne la masque pas, on la marque « à confirmer ».
    """
    if model_prob is None or market_fair is None:
        return "ok", None
    intl = domain == config.DOMAIN_INTL
    out_max = (config.VALUE_OUTSIDER_FAIR_MAX_INTL if intl
               else config.VALUE_OUTSIDER_FAIR_MAX_CLUB)
    ratio = (config.VALUE_DISAGREE_RATIO_INTL if intl
             else config.VALUE_DISAGREE_RATIO_CLUB)
    if market_fair < out_max and model_prob >= market_fair * ratio:
        reason = (f"Le marché voit cette issue en outsider (~{round(market_fair * 100)} %) "
                  f"et le modèle est en fort désaccord : value à confirmer, fiabilité "
                  f"faible{' (sélections : calibration vs marché non prouvée)' if intl else ''}.")
        return "low", reason
    return "ok", None


def _fair_market_probs(best: dict) -> dict:
    """Probabilités de marché ÉQUITABLES (dévigées) par issue, à partir des meilleurs prix.

    On retire la marge du bookmaker (devig) pour comparer honnêtement le modèle au
    marché : `1/cote` brut surévalue la croyance du marché. Renvoie {sel: proba} pour
    les issues dont le marché est complet (1X2 ou O/U)."""
    fair: dict[str, float] = {}
    h2h = best.get("h2h", {})
    trio = [h2h.get(k, {}).get("odds") for k in ("home", "draw", "away")]
    if all(o for o in trio):
        fp = devig.fair_probs([float(o) for o in trio])
        if fp:
            fair.update({"home": fp[0], "draw": fp[1], "away": fp[2]})
    tot = best.get("totals", {})
    duo = [tot.get(k, {}).get("odds") for k in ("over", "under")]
    if all(o for o in duo):
        fp = devig.fair_probs([float(o) for o in duo])
        if fp:
            fair.update({"over": fp[0], "under": fp[1]})
    return fair


def _build_rows(best: dict, model_probs: dict, home: str, away: str,
                domain: str = config.DOMAIN_CLUB) -> list[dict]:
    """Assemble la table « meilleur prix + value » à partir des cotes captées."""
    edge_thr = config.BET_EDGE_THRESHOLD
    fair = _fair_market_probs(best)
    flat = {
        "home": ("1X2", best.get("h2h", {}).get("home")),
        "draw": ("1X2", best.get("h2h", {}).get("draw")),
        "away": ("1X2", best.get("h2h", {}).get("away")),
        "over": ("OU25", best.get("totals", {}).get("over")),
        "under": ("OU25", best.get("totals", {}).get("under")),
    }
    rows = []
    for sel, (market, info) in flat.items():
        if not info:
            continue
        odds = info["odds"]
        p = model_probs.get(sel)
        edge = (p * odds - 1.0) if p is not None else None
        value = bool(edge is not None and edge > edge_thr)
        mkt_fair = fair.get(sel)
        reliability, reason = (_value_reliability(domain, p, mkt_fair)
                               if value else ("ok", None))
        rows.append({
            "market": market, "selection": sel,
            "label": _LIVE_SELECTION_LABELS[sel].format(home=home, away=away),
            "best_odds": round(float(odds), 3), "book": info.get("book"),
            "model_prob": None if p is None else round(float(p), 4),
            "implied_prob": round(1.0 / odds, 4),
            "market_fair_prob": None if mkt_fair is None else round(float(mkt_fair), 4),
            "edge": None if edge is None else round(float(edge), 4),
            "value": value,
            "reliability": reliability,
            "value_reliable": bool(value and reliability == "ok"),
            "reliability_note": reason,
        })
    return rows


def _store_snapshots(event_id, competition, home, away, rows) -> None:
    """Enregistre les meilleurs prix captés (suivi de ligne / CLV best-effort)."""
    if not event_id or not rows:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = db.connect()
    try:
        conn.executemany(
            """INSERT OR REPLACE INTO odds_snapshots
               (captured_at,event_id,competition,home,away,market,selection,best_odds,book)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [(now, event_id, competition, home, away, r["market"], r["selection"],
              r["best_odds"], r["book"]) for r in rows])
        conn.commit()
    finally:
        conn.close()


def _football_data_fallback(competition: str, home: str, away: str,
                            model_probs: dict) -> list[dict]:
    """Repli sans clé / sans événement : cotes d'OUVERTURE football-data en base.

    Cherche le dernier match connu entre ces deux équipes (peu importe l'ordre)
    avec des cotes. Marqué « historique » : ce n'est PAS un prix live.
    """
    domain = domain_of(competition)
    hid, aid = _resolve(domain, home), _resolve(domain, away)
    if hid is None or aid is None:
        return []
    conn = db.connect()
    try:
        row = conn.execute(
            """SELECT odds_home, odds_draw, odds_away, odds_open_over25, odds_open_under25, date
                 FROM matches
                WHERE domain=? AND odds_home IS NOT NULL
                  AND ((home_team_id=? AND away_team_id=?) OR (home_team_id=? AND away_team_id=?))
                ORDER BY date DESC LIMIT 1""",
            (domain, hid, aid, aid, hid)).fetchone()
    finally:
        conn.close()
    if row is None:
        return []
    best = {
        "h2h": {k: ({"odds": row[c], "book": f"football-data ({row['date']})"})
                for k, c in (("home", "odds_home"), ("draw", "odds_draw"),
                             ("away", "odds_away")) if row[c]},
        "totals": {k: ({"odds": row[c], "book": f"football-data ({row['date']})", "point": 2.5})
                   for k, c in (("over", "odds_open_over25"),
                                ("under", "odds_open_under25")) if row[c]},
    }
    return _build_rows(best, model_probs, home, away, domain=domain)


def live_odds(competition: str, home: str, away: str, neutral: bool = False,
              use_qualitative: bool | None = None) -> dict:
    """Comparateur de cotes : meilleur prix par marché + value vs modèle.

    Source live = the-odds-api (si clé). Repli propre = cotes football-data en base.
    Renvoie toujours la proba du modèle même si aucune cote n'est disponible.
    """
    pred = predict(competition, home, away, neutral=neutral, use_qualitative=use_qualitative)
    home_name, away_name = pred["home"], pred["away"]
    model_probs = _model_probs_by_selection(pred)

    base = {
        "home": home_name, "away": away_name, "competition": competition,
        "domain": pred.get("domain"),
        "edge_threshold": config.BET_EDGE_THRESHOLD,
        "configured": odds_api.configured(),
    }

    sport_key = odds_api.sport_key_for(competition)
    if odds_api.configured() and sport_key:
        events = odds_api.fetch_odds(sport_key)
        ev = odds_api.find_event(events, home_name, away_name)
        if ev:
            best = odds_api.best_prices(ev)
            rows = _build_rows(best, model_probs, home_name, away_name,
                               domain=pred.get("domain"))
            _store_snapshots(ev.get("id"), competition, home_name, away_name, rows)
            return {**base, "available": bool(rows), "source": "the-odds-api",
                    "n_books": best.get("n_books", 0),
                    "commence_time": ev.get("commence_time"),
                    "match_score": ev.get("_matched_score"),
                    "markets": rows, "quota": odds_api.last_quota()}

    # --- repli : cotes football-data (historique), clairement signalé ---
    rows = _football_data_fallback(competition, home_name, away_name, model_probs)
    reason = ("Comparateur live indisponible (pas de clé API) : repli sur les cotes "
              "football-data." if not odds_api.configured()
              else "Aucun match à venir trouvé chez les bookmakers pour cette affiche : "
                   "repli sur les cotes football-data.")
    return {**base, "available": bool(rows),
            "source": "football-data" if rows else "none",
            "reason": reason if not rows else reason,
            "n_books": 1 if rows else 0, "markets": rows,
            "quota": odds_api.last_quota()}


def odds_status() -> dict:
    """État du comparateur de cotes pour l'UI (clé présente ? quota ? cache ?)."""
    return {
        "configured": odds_api.configured(),
        "regions": config.ODDS_API_REGIONS,
        "markets": config.ODDS_API_MARKETS,
        "cache_ttl_hours": config.ODDS_API_TTL_HOURS,
        "covered_competitions": sorted(odds_api.SPORT_KEYS.keys()),
        "quota": odds_api.last_quota(),
    }


def odds_clv_summary(limit: int = 100) -> dict:
    """Suivi de ligne capté (proxy de CLV) : 1er prix vs dernier prix observé.

    Vraie CLV = prix d'entrée / cote de CLÔTURE − 1. Sans planificateur permanent,
    on rapporte le mouvement entre notre PREMIER et notre DERNIER prix capté pour
    chaque issue : positif = la ligne s'est resserrée en notre faveur. Best-effort,
    honnête sur sa limite.
    """
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT event_id, competition, home, away, market, selection,
                      best_odds, captured_at
                 FROM odds_snapshots ORDER BY captured_at""").fetchall()
    finally:
        conn.close()
    series: dict[tuple, list] = {}
    for r in rows:
        series.setdefault(
            (r["event_id"], r["market"], r["selection"]), []).append(r)
    out = []
    for (eid, market, sel), snaps in series.items():
        if len(snaps) < 2:
            continue
        first, last = snaps[0], snaps[-1]
        first_o, last_o = first["best_odds"], last["best_odds"]
        if not first_o or first_o <= 1.0:
            continue
        # On a « battu la ligne » si on avait capté une cote plus haute que la dernière.
        move = first_o / last_o - 1.0
        out.append({
            "competition": first["competition"],
            "match": f'{first["home"]} — {first["away"]}',
            "market": market, "selection": sel,
            "first_odds": first_o, "last_odds": last_o,
            "captured_clv": round(move, 4), "n_snapshots": len(snaps),
        })
    out.sort(key=lambda x: x["captured_clv"], reverse=True)
    beat = [o for o in out if o["captured_clv"] > 0]
    return {
        "n_tracked": len(out),
        "beat_rate": round(len(beat) / len(out), 4) if out else None,
        "mean_captured_clv": round(sum(o["captured_clv"] for o in out) / len(out), 4) if out else None,
        "items": out[:limit],
    }


@functools.lru_cache(maxsize=2)
def _players_index(domain: str) -> dict:
    """team_id -> liste des joueurs de la SAISON LA PLUS RÉCENTE disponible.

    On ne garde que la dernière saison de chaque équipe : c'est l'effectif le plus
    représentatif de la production actuelle (les partants ne polluent pas le calcul).
    """
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT p.team_id, p.player_name, p.season, p.games, p.minutes,
                      p.goals, p.npg, p.xg, p.npxg, p.position
                 FROM players p
                 JOIN (SELECT team_id, MAX(season) AS s FROM players GROUP BY team_id) m
                   ON p.team_id = m.team_id AND p.season = m.s""").fetchall()
    except Exception:
        conn.close()
        return {}
    conn.close()
    index: dict[str, list[dict]] = {}
    for r in rows:
        index.setdefault(r["team_id"], []).append({
            "player_name": r["player_name"], "games": r["games"], "minutes": r["minutes"],
            "goals": r["goals"], "npg": r["npg"], "xg": r["xg"], "npxg": r["npxg"],
            "position": r["position"], "season": r["season"],
        })
    return index


def predict_scorers(competition: str, home: str, away: str, neutral: bool = False,
                    top_n: int = 8, use_qualitative: bool | None = None,
                    unavailable_home: list[str] | None = None,
                    unavailable_away: list[str] | None = None) -> dict:
    """Buteurs probables (anytime scorer) pour un match.

    Clubs : liste classée par équipe. Sélections : données joueur trop pauvres ->
    `available=False` (jamais de fausse précision). Le lambda de chaque équipe vient
    du même modèle que la prédiction 1/X/2 (couche actu incluse si activée).
    """
    domain = domain_of(competition)
    if domain != config.DOMAIN_CLUB:
        return {"available": False, "domain": domain,
                "reason": "Données joueur indisponibles pour les sélections : "
                          "fiabilité insuffisante, aucune estimation par buteur.",
                "home": {}, "away": {}}

    # Réutilise exactement le pipeline de prédiction (lambda par équipe + couche actu).
    pred = predict(competition, home, away, neutral=neutral, use_qualitative=use_qualitative)
    hid = _resolve(domain, home)
    aid = _resolve(domain, away)
    index = _players_index(domain)

    home_players = index.get(hid, [])
    away_players = index.get(aid, [])
    home_list = scorers.distribute(home_players, pred["exp_home_goals"],
                                   exclude=set(unavailable_home or []), top_n=top_n)
    away_list = scorers.distribute(away_players, pred["exp_away_goals"],
                                   exclude=set(unavailable_away or []), top_n=top_n)

    return {
        "available": bool(home_list or away_list),
        "domain": domain,
        "competition": competition,
        "exp_home_goals": pred["exp_home_goals"],
        "exp_away_goals": pred["exp_away_goals"],
        "home": {"team": pred["home"], "scorers": home_list,
                 "data": bool(home_players)},
        "away": {"team": pred["away"], "scorers": away_list,
                 "data": bool(away_players)},
    }


def app_meta() -> dict:
    """Métadonnées légères pour l'en-tête : date de dernière mise à jour réussie.

    `last_updated` = horodatage du dernier refresh réussi (table app_meta). En repli
    (jamais rafraîchi sur cette machine), on expose la date du dernier match en base,
    qui reflète aussi la fraîcheur des données livrées.
    """
    from . import refresh_job  # import tardif : refresh_job importe déjà service.
    conn = db.connect()
    try:
        row = conn.execute("SELECT MAX(date) FROM matches").fetchone()
        latest = row[0] if row else None
    except Exception:
        latest = None
    finally:
        conn.close()
    return {"last_updated": refresh_job.last_updated(), "latest_match_date": latest}


# ---------------------------------------------------------------------------
# Étape 3 : affiches des prochains jours, toutes compétitions couvertes.
# ---------------------------------------------------------------------------
# Cache process (préserve le quota the-odds-api : une clé a un quota mensuel).
_UPCOMING_CACHE: dict = {"at": 0.0, "days": None, "data": None}


def _parse_dt(value) -> datetime | None:
    """Parse une date ISO (avec ou sans heure / 'Z') en datetime UTC. None sinon."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fixtures_in_window(now: datetime, horizon: datetime) -> list[dict]:
    """Repli sans clé : affiches de la table `fixtures` dans la fenêtre demandée."""
    conn = db.connect()
    try:
        df = pd.read_sql_query(
            """SELECT f.date, f.competition, f.neutral,
                      th.canonical_name AS home, ta.canonical_name AS away,
                      f.home_team_id, f.away_team_id
                 FROM fixtures f
                 JOIN teams th ON th.team_id = f.home_team_id
                 JOIN teams ta ON ta.team_id = f.away_team_id
                ORDER BY f.date""", conn)
    finally:
        conn.close()
    out = []
    for r in df.to_dict(orient="records"):
        d = _parse_dt(r["date"])
        if d is None or d.date() < now.date() or d.date() > horizon.date():
            continue
        domain = domain_of(r["competition"])
        out.append({
            "commence_time": None,
            "date": d.date().isoformat(),
            "competition": r["competition"], "domain": domain,
            "home_team_id": r["home_team_id"], "away_team_id": r["away_team_id"],
            "home": r["home"], "away": r["away"],
            "home_badge": badges.badge(r["home"], domain),
            "away_badge": badges.badge(r["away"], domain),
            "neutral": bool(r["neutral"]),
            "has_odds": False,
        })
    return out


def upcoming_matches(days: int = 7) -> dict:
    """Affiches à venir des prochains jours, toutes compétitions couvertes.

    Source live = the-odds-api (événements + meilleures cotes captées). Repli propre
    = table `fixtures` (sans cotes). Résultat mis en cache au niveau process
    (TTL = ODDS_API_TTL_HOURS) pour préserver le quota d'appels.
    """
    import time

    days = max(1, min(30, int(days)))
    now_ts = time.time()
    ttl = float(config.ODDS_API_TTL_HOURS) * 3600.0
    c = _UPCOMING_CACHE
    if c["data"] is not None and c["days"] == days and now_ts - c["at"] < ttl:
        return c["data"]

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    matches: list[dict] = []
    source = "none"

    if odds_api.configured():
        source = "the-odds-api"
        seen: set[tuple] = set()
        for competition, sport_key in odds_api.SPORT_KEYS.items():
            domain = domain_of(competition)
            for ev in odds_api.fetch_odds(sport_key):
                ct = _parse_dt(ev.get("commence_time"))
                if ct is None or ct < now or ct > horizon:
                    continue
                hid = _resolve_fuzzy(domain, (ev.get("home_team") or "").strip())
                aid = _resolve_fuzzy(domain, (ev.get("away_team") or "").strip())
                if hid is None or aid is None or hid == aid:
                    continue
                key = (competition, hid, aid, ct.date().isoformat())
                if key in seen:
                    continue
                seen.add(key)
                tn = _team_names(domain)
                home_name, away_name = tn.get(hid, hid), tn.get(aid, aid)
                best = odds_api.best_prices(ev)
                matches.append({
                    "commence_time": ev.get("commence_time"),
                    "date": ct.date().isoformat(),
                    "competition": competition, "domain": domain,
                    "home_team_id": hid, "away_team_id": aid,
                    "home": home_name, "away": away_name,
                    "home_badge": badges.badge(home_name, domain),
                    "away_badge": badges.badge(away_name, domain),
                    "neutral": False,
                    "has_odds": bool(best.get("h2h")),
                })

    if not matches:
        fb = _fixtures_in_window(now, horizon)
        if fb:
            matches, source = fb, "fixtures"

    matches.sort(key=lambda m: (m.get("commence_time") or m.get("date") or "",
                                m.get("competition") or ""))
    result = {"days": days, "source": source,
              "configured": odds_api.configured(),
              "count": len(matches), "matches": matches}
    c.update({"at": now_ts, "days": days, "data": result})
    return result


# ---------------------------------------------------------------------------
# Étape 4 : « Meilleure value du jour » — le meilleur edge parmi les affiches.
# ---------------------------------------------------------------------------
_VALUE_CACHE: dict = {"at": 0.0, "days": None, "data": None}

# Garde-fous d'honnêteté : c'est sur les outsiders extrêmes que le modèle est le moins
# fiable (biais favori/outsider, value betting négatif au backtest). Un mini-écart de
# proba y fabrique un edge gigantesque mais illusoire. On ignore donc les issues trop
# improbables et les cotes de pur loto pour ne remonter que des value crédibles.
_VALUE_MIN_MODEL_PROB = 0.12
_VALUE_MAX_ODDS = 8.0


def best_value_today(days: int = 3, top_n: int = 3, max_scan: int = 60) -> dict:
    """Balaye les affiches à venir (avec cotes live) et remonte les meilleures value.

    Pour chaque match couvert par the-odds-api, on réutilise exactement
    `live_odds` (proba du modèle × meilleure cote → edge), puis on garde les issues
    dont l'edge dépasse le seuil. On les classe par edge décroissant et on renvoie
    le top N. Résultat mis en cache (même TTL que les cotes : quota préservé).

    Honnêteté : une « value » signale un prix favorable selon le modèle, **jamais**
    une garantie de gain. Rien n'est remonté si aucune value n'est trouvée.
    """
    import time

    days = max(1, min(14, int(days)))
    top_n = max(1, min(10, int(top_n)))
    now_ts = time.time()
    ttl = float(config.ODDS_API_TTL_HOURS) * 3600.0
    c = _VALUE_CACHE
    if c["data"] is not None and c["days"] == days and now_ts - c["at"] < ttl:
        return c["data"]

    up = upcoming_matches(days)
    candidates: list[dict] = []
    n_flagged = 0          # value écartées car peu fiables (outsider + fort désaccord)
    scanned = 0
    for m in up["matches"]:
        if not m.get("has_odds") or scanned >= max_scan:
            continue
        scanned += 1
        try:
            od = live_odds(m["competition"], m["home"], m["away"],
                           neutral=m.get("neutral", False))
        except Exception:  # noqa: BLE001
            continue
        if od.get("source") != "the-odds-api":
            continue
        for r in od.get("markets", []):
            p = r.get("model_prob")
            if not (r.get("value") and r.get("edge") is not None and p is not None
                    and p >= _VALUE_MIN_MODEL_PROB
                    and r.get("best_odds", 99) <= _VALUE_MAX_ODDS):
                continue
            # Garde-fou anti fausse-value : on ne met PAS en avant une value jugée
            # peu fiable (outsider sur lequel le modèle est en fort désaccord).
            if not r.get("value_reliable", True):
                n_flagged += 1
                continue
            candidates.append({
                "competition": m["competition"], "domain": m["domain"],
                "home": m["home"], "away": m["away"],
                "home_team_id": m["home_team_id"], "away_team_id": m["away_team_id"],
                "home_badge": m["home_badge"], "away_badge": m["away_badge"],
                "date": m["date"], "commence_time": m["commence_time"],
                "neutral": bool(m.get("neutral", False)),
                "selection": r["selection"], "label": r["label"],
                "best_odds": r["best_odds"], "book": r["book"],
                "model_prob": r["model_prob"], "edge": r["edge"],
            })
    candidates.sort(key=lambda x: x["edge"], reverse=True)
    result = {
        "configured": odds_api.configured(),
        "edge_threshold": config.BET_EDGE_THRESHOLD,
        "scanned": scanned, "n_value": len(candidates),
        "n_flagged": n_flagged,
        "items": candidates[:top_n],
    }
    c.update({"at": now_ts, "days": days, "data": result})
    return result


# ---------------------------------------------------------------------------
# Étape 6 : « Track record » — performances réelles issues du backtest honnête.
# ---------------------------------------------------------------------------
def track_record() -> dict:
    """Métriques de performance RÉELLES du backtest walk-forward (jamais inventées).

    Lit `data/backtest_result.json` (produit par `python -m pipeline.backtest`) :
    RPS modèle vs bookmaker, ROI du value betting, courbe de calibration. Renvoie
    `available=False` proprement si l'artefact n'a pas encore été généré.
    """
    import json
    import os

    path = os.path.join(config.ROOT, "data", "backtest_result.json")
    backtest = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                backtest = json.load(fh)
        except (OSError, ValueError):
            backtest = None

    # Performance RÉELLE issue du journal des prédictions (distincte du backtest).
    try:
        live = journal.live_track_record()
    except Exception:  # noqa: BLE001
        live = {"available": False}

    out = {"available": backtest is not None, "live": live}
    if backtest is not None:
        out.update(backtest)
    return out


def qualitative_status() -> dict:
    """État de la couche actu pour l'UI : défaut, compteur du jour, garde-fous coût."""
    return {
        "enabled_default": bool(_qualitative.enabled),
        "calls_today": _qualitative.calls_today(),
        "cache_ttl_hours": config.QUALITATIVE_CACHE_TTL_HOURS,
        "max_web_uses": config.QUALITATIVE_WEB_SEARCH_MAX_USES,
        "news_window_days": config.QUALITATIVE_NEWS_WINDOW_DAYS,
    }


def clear_caches() -> None:
    """À appeler après un ré-entraînement / refresh."""
    _predictor.cache_clear()
    _snapshot.cache_clear()
    _team_names.cache_clear()
    _players_index.cache_clear()
    _PRED_CACHE.clear()
    _UPCOMING_CACHE.update({"at": 0.0, "days": None, "data": None})
    _VALUE_CACHE.update({"at": 0.0, "days": None, "data": None})
    journal.clear_version_cache()      # la version de modèle change au réentraînement
