"""Service de prédiction : la fonction pure du contrat.

`predict(competition, home, away, neutral)` renvoie le dictionnaire complet du
contrat de sortie (1/X/2 calibré, buts attendus, score le plus probable, matrice
des scores, over/under 2.5, BTTS). C'est le cœur réutilisé par l'API (Phase 4).

Le service charge paresseusement, depuis data/models/, les artefacts entraînés
(Dixon-Coles + XGBoost + calibration) et, depuis la base, l'état courant des équipes
(forme, Elo, xG) pour fabriquer le vecteur de features du match demandé.
"""

from __future__ import annotations

import functools

import pandas as pd

from datetime import datetime, timezone

from . import config, db, features, odds_api, scorers, staking
from .qualitative import QualitativeLayer
from .train import load_predictor

# Compétitions de clubs reconnues (le reste est traité comme sélections).
_CLUB_COMPETITIONS = {lg["competition"] for lg in config.CLUB_LEAGUES}

_qualitative = QualitativeLayer()


def domain_of(competition: str) -> str:
    return config.DOMAIN_CLUB if competition in _CLUB_COMPETITIONS else config.DOMAIN_INTL


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


def list_teams(domain: str | None = None) -> list[dict]:
    domains = [domain] if domain else [config.DOMAIN_CLUB, config.DOMAIN_INTL]
    out = []
    for d in domains:
        names = _team_names(d)
        snap = _snapshot(d)
        for tid, nm in sorted(names.items(), key=lambda kv: kv[1]):
            if tid in snap:      # uniquement les équipes avec un historique
                out.append({"id": tid, "name": nm, "domain": d,
                            "elo": round(snap[tid]["elo"], 1)})
    return out


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

    feat = features.make_match_features(
        snap[hid], snap[aid], neutral, competition, is_club)

    names = _team_names(domain)
    home_name, away_name = names.get(hid, hid), names.get(aid, aid)

    # Couche qualitative optionnelle (off par défaut). `use_qualitative` permet de
    # l'activer/désactiver par requête depuis l'UI, sans toucher au .env.
    adjustment = _qualitative.adjust(home_name, away_name, competition,
                                     enabled_override=use_qualitative)

    pred = _predictor(domain).predict(hid, aid, neutral=neutral, feat=feat,
                                      adjustment=adjustment)
    pred["home"] = home_name
    pred["away"] = away_name
    pred["competition"] = competition
    pred["domain"] = domain
    # État EFFECTIF de la couche pour cette requête (l'UI affiche le bon panneau).
    effective = (_qualitative.enabled if use_qualitative is None
                 else bool(use_qualitative))
    pred["qualitative_enabled"] = effective
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


def _build_rows(best: dict, model_probs: dict, home: str, away: str) -> list[dict]:
    """Assemble la table « meilleur prix + value » à partir des cotes captées."""
    edge_thr = config.BET_EDGE_THRESHOLD
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
        rows.append({
            "market": market, "selection": sel,
            "label": _LIVE_SELECTION_LABELS[sel].format(home=home, away=away),
            "best_odds": round(float(odds), 3), "book": info.get("book"),
            "model_prob": None if p is None else round(float(p), 4),
            "implied_prob": round(1.0 / odds, 4),
            "edge": None if edge is None else round(float(edge), 4),
            "value": bool(edge is not None and edge > edge_thr),
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
    return _build_rows(best, model_probs, home, away)


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
            rows = _build_rows(best, model_probs, home_name, away_name)
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
