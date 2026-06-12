"""Feature engineering SANS fuite de données.

Règle absolue : la ligne de features d'un match n'utilise QUE des informations
connues avant le coup d'envoi. On garantit ça en parcourant les matchs dans
l'ordre chronologique et en ne lisant, pour chaque match, que l'état des équipes
construit à partir des matchs STRICTEMENT antérieurs (l'état est mis à jour après
avoir produit la ligne).

Features produites (toutes « avant match ») :
  - Elo domicile / extérieur et différence d'Elo (feature la plus prédictive)
  - forme sur 5 et 10 matchs : points par match, buts marqués / encaissés moyens
  - xG / xGA glissants sur 5 matchs (clubs uniquement ; NaN pour les sélections)
  - jours de repos depuis le dernier match
  - nombre de matchs déjà joués (fiabilité de la forme)
  - terrain neutre, importance de la compétition
  - confrontations directes récentes (poids faible) : taux de victoire et diff. de buts

La fonction renvoie un DataFrame indexé par match_id et peut le matérialiser dans
la table SQLite `features`.
"""

from __future__ import annotations

from collections import deque

import numpy as np
import pandas as pd

from . import config, context, db, devig, elo, squad_value, weather

FORM_SHORT = 5
FORM_LONG = 10
XG_WINDOW = 5
H2H_WINDOW = 5
SHOT_WINDOW = 5          # P6 : fenêtre glissante des proxys de qualité de tir
CONGESTION_DAYS = 14     # P6 : fenêtre de congestion (matchs récents)

# Importance de la compétition (échelle simple, réutilise l'esprit du moteur Elo).
_INTL_IMPORTANCE = {
    "FIFA World Cup": 1.0,
    "Copa America": 0.85, "UEFA Euro": 0.85, "African Cup of Nations": 0.85,
    "AFC Asian Cup": 0.85, "Gold Cup": 0.8,
    "UEFA Nations League": 0.7,
    "FIFA World Cup qualification": 0.65, "UEFA Euro qualification": 0.6,
    "Friendly": 0.3,
}
_DEFAULT_INTL_IMPORTANCE = 0.5
_CLUB_IMPORTANCE = 0.75  # match de championnat


def _importance(competition: str, is_club: bool) -> float:
    if is_club:
        return _CLUB_IMPORTANCE
    return _INTL_IMPORTANCE.get(competition, _DEFAULT_INTL_IMPORTANCE)


def _points(gf: int, ga: int) -> int:
    return 3 if gf > ga else (1 if gf == ga else 0)


def _squad_value_feats(home_val, away_val) -> dict:
    """Features de valeur d'effectif (M€) + log-ratio, NaN si une valeur manque."""
    hv = float(home_val) if home_val and home_val > 0 else np.nan
    av = float(away_val) if away_val and away_val > 0 else np.nan
    if not (_isna(hv) or _isna(av)):
        logratio = float(np.log(hv / av))
    else:
        logratio = np.nan
    return {"home_squad_value": hv, "away_squad_value": av,
            "squad_value_logratio": logratio}


# --- Phase P6 : contexte (qualité de tir, congestion, déplacement, météo) -----
# Clés produites par les features P6, listées une seule fois pour rester cohérent
# entre le chemin d'entraînement (build_features) et le chemin de prédiction live.
_P6_KEYS = (
    # Groupe A : proxys de qualité de tir (glissants, clubs ; NaN pour sélections)
    "home_xg_per_shot", "away_xg_per_shot",
    "home_sot_ratio", "away_sot_ratio",
    "home_finishing", "away_finishing",
    "home_def_xg_per_shot", "away_def_xg_per_shot",
    # Groupe B : congestion du calendrier + distance de déplacement
    "home_matches_14d", "away_matches_14d",
    "home_travel_km", "away_travel_km",
    # Groupe C : météo au lieu du match (identique pour les deux équipes)
    "temp_c", "precip_mm", "wind_kmh",
)


def _p6_blank() -> dict:
    return {k: np.nan for k in _P6_KEYS}


def _weather_feats(wx: dict | None) -> dict:
    """Extrait température / pluie / vent d'une entrée météo (NaN si absente)."""
    wx = wx or {}
    return {
        "temp_c": _num(wx.get("temp_c")),
        "precip_mm": _num(wx.get("precip_mm")),
        "wind_kmh": _num(wx.get("wind_kmh")),
    }


def _num(v):
    return float(v) if v is not None else np.nan


def _market_probs(oh, od, oa) -> dict:
    """Probabilités 1X2 de marché dévigées depuis les cotes d'OUVERTURE (sans fuite)."""
    fair = devig.fair_probs([oh, od, oa], method="power") if not (
        _isna(oh) or _isna(od) or _isna(oa)) else None
    if fair is None:
        return {"p_mkt_home": np.nan, "p_mkt_draw": np.nan, "p_mkt_away": np.nan}
    return {"p_mkt_home": fair[0], "p_mkt_draw": fair[1], "p_mkt_away": fair[2]}


def load_matches(conn, domain: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT * FROM matches WHERE domain = ? ORDER BY date, match_id",
        conn, params=(domain,), parse_dates=["date"],
    )
    return df


def build_features(conn, domain: str) -> pd.DataFrame:
    is_club = domain == config.DOMAIN_CLUB
    df = load_matches(conn, domain)
    if df.empty:
        return df

    # Elo pré-match (sans fuite : elo.compute_elo expose le rating AVANT le match).
    # Alignement par match_id (jamais par position) pour rester robuste à l'ordre
    # de tri interne lorsqu'une même date porte plusieurs matchs.
    elo_in = df.rename(columns={"home_team_id": "home_team", "away_team_id": "away_team"})
    elo_df = elo.compute_elo(elo_in, is_club=is_club)
    df["home_elo"] = df["match_id"].map(dict(zip(elo_df["match_id"], elo_df["home_elo"])))
    df["away_elo"] = df["match_id"].map(dict(zip(elo_df["match_id"], elo_df["away_elo"])))

    # Phase P5 : Elo offensif/défensif (pré-match, aligné par match_id, sans fuite).
    od = elo.compute_elo_offdef(elo_in, is_club=is_club)
    for col in ("home_off_elo", "home_def_elo", "away_off_elo", "away_def_elo"):
        df[col] = df["match_id"].map(dict(zip(od["match_id"], od[col])))

    # Phase P5 : valeur marchande des effectifs (proxy de niveau, structurel pré-match).
    sval = squad_value.values_by_team_id(conn, domain)

    # Phase P6 : lieux (coordonnées) pour déplacement + météo. Match non neutre ->
    # lieu = stade du domicile ; match neutre ou lieu inconnu -> coords None.
    coords = context.coords_by_team_id(conn, domain)

    def _venue(row):
        if int(row.neutral):
            return None
        return coords.get(row.home_team_id)

    # Météo : batch UN appel par lieu sur toute la plage de dates (cache disque).
    wx_rows = []
    for r in df.itertuples(index=False):
        v = _venue(r)
        if v is not None:
            wx_rows.append({"match_id": r.match_id, "lat": v[0], "lon": v[1],
                            "date": pd.Timestamp(r.date).strftime("%Y-%m-%d")})
    wx = weather.weather_by_match(wx_rows)

    # État glissant par équipe (uniquement le passé).
    hist: dict[str, deque] = {}     # team -> deque de dicts {gf,ga,pts,xgf,xga,sh,sot,sha}
    last_date: dict[str, pd.Timestamp] = {}
    played: dict[str, int] = {}
    h2h: dict[tuple, deque] = {}    # (a,b triés) -> deque de (home_id, gd_home)
    recent_dates: dict[str, deque] = {}   # P6 congestion : dates des matchs récents
    last_venue: dict[str, tuple] = {}     # P6 déplacement : dernier lieu connu

    out_rows = []
    for r in df.itertuples(index=False):
        h, a = r.home_team_id, r.away_team_id
        fh = _team_form(hist.get(h))
        fa = _team_form(hist.get(a))

        rest_h = (r.date - last_date[h]).days if h in last_date else np.nan
        rest_a = (r.date - last_date[a]).days if a in last_date else np.nan

        hh, hd = _h2h_stats(h2h.get(_pair(h, a)), h)

        sv = _squad_value_feats(sval.get(h), sval.get(a))

        # --- Phase P6 : congestion + déplacement (uniquement le passé) ---
        cong_h = _congestion(recent_dates.get(h), r.date)
        cong_a = _congestion(recent_dates.get(a), r.date)
        venue = _venue(r)
        trav_h = _travel(last_venue.get(h), venue)
        trav_a = _travel(last_venue.get(a), venue)
        p6 = {
            "home_xg_per_shot": fh["xg_per_shot"], "away_xg_per_shot": fa["xg_per_shot"],
            "home_sot_ratio": fh["sot_ratio"], "away_sot_ratio": fa["sot_ratio"],
            "home_finishing": fh["finishing"], "away_finishing": fa["finishing"],
            "home_def_xg_per_shot": fh["def_xg_per_shot"],
            "away_def_xg_per_shot": fa["def_xg_per_shot"],
            "home_matches_14d": cong_h, "away_matches_14d": cong_a,
            "home_travel_km": trav_h, "away_travel_km": trav_a,
            **_weather_feats(wx.get(r.match_id)),
        }

        out_rows.append({
            "match_id": r.match_id, "date": r.date, "competition": r.competition,
            "domain": domain, "season": r.season,
            "home_team_id": h, "away_team_id": a,
            "home_goals": r.home_goals, "away_goals": r.away_goals,
            "neutral": int(r.neutral),
            "comp_importance": _importance(r.competition, is_club),
            "home_elo": r.home_elo, "away_elo": r.away_elo,
            "elo_diff": r.home_elo - r.away_elo,
            # Phase P5 : Elo offensif/défensif + avantage terrain par compétition + valeur d'effectif
            "home_off_elo": r.home_off_elo, "away_off_elo": r.away_off_elo,
            "home_def_elo": r.home_def_elo, "away_def_elo": r.away_def_elo,
            "off_def_diff": (r.home_off_elo - r.away_def_elo) - (r.away_off_elo - r.home_def_elo),
            "home_advantage": elo.home_advantage_for(r.competition, is_club, bool(r.neutral)),
            **sv,
            "home_played": played.get(h, 0), "away_played": played.get(a, 0),
            "home_rest_days": rest_h, "away_rest_days": rest_a,
            # forme courte
            "home_form5_ppg": fh["ppg5"], "away_form5_ppg": fa["ppg5"],
            "home_form5_gf": fh["gf5"], "away_form5_gf": fa["gf5"],
            "home_form5_ga": fh["ga5"], "away_form5_ga": fa["ga5"],
            # forme longue
            "home_form10_ppg": fh["ppg10"], "away_form10_ppg": fa["ppg10"],
            "home_form10_gf": fh["gf10"], "away_form10_gf": fa["gf10"],
            "home_form10_ga": fh["ga10"], "away_form10_ga": fa["ga10"],
            # xG glissant (clubs)
            "home_xg5": fh["xgf5"], "away_xg5": fa["xgf5"],
            "home_xga5": fh["xga5"], "away_xga5": fa["xga5"],
            # confrontations directes récentes (point de vue domicile actuel)
            "h2h_home_winrate": hh, "h2h_home_gd": hd,
            # cotes bookmaker (benchmark + base des features de marché P2)
            "odds_home": r.odds_home, "odds_draw": r.odds_draw, "odds_away": r.odds_away,
            # probabilités de marché DÉVIGÉES (cotes d'OUVERTURE -> pré-match, sans fuite).
            # Stockées toujours ; utilisées comme features XGBoost seulement si activé (P2).
            **_market_probs(r.odds_home, r.odds_draw, r.odds_away),
            # Phase P6 : contexte (qualité de tir, congestion, déplacement, météo).
            # Toujours stockées ; entrées du modèle seulement si activé (P6).
            **p6,
        })

        # --- mise à jour de l'état APRÈS avoir produit la ligne (anti-fuite) ---
        xgf_h = r.home_xg if not _isna(r.home_xg) else np.nan
        xga_h = r.away_xg if not _isna(r.away_xg) else np.nan
        sh_h = r.home_shots if not _isna(r.home_shots) else np.nan
        sh_a = r.away_shots if not _isna(r.away_shots) else np.nan
        sot_h = r.home_sot if not _isna(r.home_sot) else np.nan
        sot_a = r.away_sot if not _isna(r.away_sot) else np.nan
        # tirs de l'équipe (sh), cadrés (sot), et tirs concédés (sha = tirs adverses)
        _push(hist, h, r.home_goals, r.away_goals, xgf_h, xga_h, sh_h, sot_h, sh_a)
        _push(hist, a, r.away_goals, r.home_goals, xga_h, xgf_h, sh_a, sot_a, sh_h)
        last_date[h] = r.date
        last_date[a] = r.date
        played[h] = played.get(h, 0) + 1
        played[a] = played.get(a, 0) + 1
        recent_dates.setdefault(h, deque()).append(r.date)
        recent_dates.setdefault(a, deque()).append(r.date)
        if venue is not None:
            last_venue[h] = venue
            last_venue[a] = venue
        h2h.setdefault(_pair(h, a), deque(maxlen=H2H_WINDOW)).append(
            (h, r.home_goals - r.away_goals))

    feats = pd.DataFrame(out_rows).set_index("match_id")
    return feats


# --- helpers d'état --------------------------------------------------------
def _push(hist, team, gf, ga, xgf, xga, sh=np.nan, sot=np.nan, sha=np.nan):
    dq = hist.setdefault(team, deque(maxlen=FORM_LONG))
    dq.append({"gf": gf, "ga": ga, "pts": _points(gf, ga), "xgf": xgf, "xga": xga,
               "sh": sh, "sot": sot, "sha": sha})


_FORM_KEYS = ["ppg5", "gf5", "ga5", "ppg10", "gf10", "ga10", "xgf5", "xga5",
              "xg_per_shot", "sot_ratio", "finishing", "def_xg_per_shot"]


def _team_form(dq) -> dict:
    if not dq:
        return {k: np.nan for k in _FORM_KEYS}
    items = list(dq)
    s5, s10 = items[-FORM_SHORT:], items[-FORM_LONG:]
    sh = items[-SHOT_WINDOW:]

    def _avg(lst, key):
        vals = [x[key] for x in lst if not (isinstance(x[key], float) and np.isnan(x[key]))]
        return float(np.mean(vals)) if vals else np.nan

    def _ratio_avg(lst, num, den):
        # P6 : moyenne des ratios par match (robuste : on saute les matchs sans tirs)
        vals = []
        for x in lst:
            n, d = x[num], x[den]
            if not _isna(n) and not _isna(d) and d > 0:
                vals.append(n / d)
        return float(np.mean(vals)) if vals else np.nan

    def _diff_avg(lst, a, b):
        vals = []
        for x in lst:
            if not _isna(x[a]) and not _isna(x[b]):
                vals.append(x[a] - x[b])
        return float(np.mean(vals)) if vals else np.nan

    return {
        "ppg5": _avg(s5, "pts"), "gf5": _avg(s5, "gf"), "ga5": _avg(s5, "ga"),
        "ppg10": _avg(s10, "pts"), "gf10": _avg(s10, "gf"), "ga10": _avg(s10, "ga"),
        "xgf5": _avg(s5, "xgf"), "xga5": _avg(s5, "xga"),
        # P6 — proxys de qualité de tir (glissants sur SHOT_WINDOW) :
        "xg_per_shot": _ratio_avg(sh, "xgf", "sh"),       # qualité offensive du tir
        "sot_ratio": _ratio_avg(sh, "sot", "sh"),         # précision (tirs cadrés)
        "finishing": _diff_avg(sh, "gf", "xgf"),          # sur/sous-performance vs xG
        "def_xg_per_shot": _ratio_avg(sh, "xga", "sha"),  # qualité des tirs concédés
    }


def _pair(a, b):
    return (a, b) if a <= b else (b, a)


def _h2h_stats(dq, home_id):
    if not dq:
        return np.nan, np.nan
    wins, gds = [], []
    for prev_home, gd in dq:
        # diff de buts du point de vue de l'équipe qui reçoit aujourd'hui
        signed = gd if prev_home == home_id else -gd
        gds.append(signed)
        wins.append(1.0 if signed > 0 else 0.0)
    return float(np.mean(wins)), float(np.mean(gds))


def _congestion(dq, now) -> float:
    """P6 : nombre de matchs joués dans les CONGESTION_DAYS jours précédant `now`.

    Compte uniquement le passé strict (dates déjà empilées avant ce match). 0 si
    aucune date (première apparition) -> congestion nulle, pas NaN.
    """
    if not dq:
        return 0.0
    cutoff = now - pd.Timedelta(days=CONGESTION_DAYS)
    return float(sum(1 for d in dq if cutoff <= d < now))


def _travel(prev_venue, cur_venue) -> float:
    """P6 : distance (km) du lieu précédent au lieu courant. NaN si l'un est inconnu."""
    if prev_venue is None or cur_venue is None:
        return np.nan
    return context.haversine_km(prev_venue[0], prev_venue[1],
                                cur_venue[0], cur_venue[1])


def _isna(v):
    return v is None or (isinstance(v, float) and np.isnan(v))


# --- état courant (pour prédire un match futur) ----------------------------
def team_state_snapshot(conn, domain: str) -> dict:
    """État le plus récent de chaque équipe APRÈS tous ses matchs connus.

    Sert à fabriquer le vecteur de features d'un match à venir : on prend la forme,
    le xG glissant, l'Elo et la date du dernier match de chaque équipe. Renvoie
    {team_id: {form/elo/last_date/played...}}.
    """
    is_club = domain == config.DOMAIN_CLUB
    df = load_matches(conn, domain)
    snap: dict[str, dict] = {}
    if df.empty:
        return snap

    elo_in = df.rename(columns={"home_team_id": "home_team", "away_team_id": "away_team"})
    final_elo = elo.compute_elo(elo_in, is_club=is_club).attrs["final_elo"]
    final_offdef = elo.compute_elo_offdef(elo_in, is_club=is_club).attrs["final_offdef"]
    sval = squad_value.values_by_team_id(conn, domain)
    coords = context.coords_by_team_id(conn, domain)

    hist: dict[str, deque] = {}
    last_date: dict[str, pd.Timestamp] = {}
    played: dict[str, int] = {}
    recent_dates: dict[str, deque] = {}
    last_venue: dict[str, tuple] = {}
    for r in df.itertuples(index=False):
        h, a = r.home_team_id, r.away_team_id
        xgf_h = r.home_xg if not _isna(r.home_xg) else np.nan
        xga_h = r.away_xg if not _isna(r.away_xg) else np.nan
        sh_h = r.home_shots if not _isna(r.home_shots) else np.nan
        sh_a = r.away_shots if not _isna(r.away_shots) else np.nan
        sot_h = r.home_sot if not _isna(r.home_sot) else np.nan
        sot_a = r.away_sot if not _isna(r.away_sot) else np.nan
        _push(hist, h, r.home_goals, r.away_goals, xgf_h, xga_h, sh_h, sot_h, sh_a)
        _push(hist, a, r.away_goals, r.home_goals, xga_h, xgf_h, sh_a, sot_a, sh_h)
        last_date[h] = r.date
        last_date[a] = r.date
        played[h] = played.get(h, 0) + 1
        played[a] = played.get(a, 0) + 1
        recent_dates.setdefault(h, deque()).append(r.date)
        recent_dates.setdefault(a, deque()).append(r.date)
        venue = None if int(r.neutral) else coords.get(h)
        if venue is not None:
            last_venue[h] = venue
            last_venue[a] = venue

    for team in set(df["home_team_id"]) | set(df["away_team_id"]):
        f = _team_form(hist.get(team))
        od = final_offdef.get(team, {"off": elo.INITIAL_RATING, "def": elo.INITIAL_RATING})
        sv = sval.get(team)
        snap[team] = {
            "elo": final_elo.get(team, elo.INITIAL_RATING),
            "off_elo": od["off"], "def_elo": od["def"],
            "squad_value": float(sv) if sv and sv > 0 else np.nan,
            "played": played.get(team, 0),
            "last_date": last_date.get(team),
            "form5_ppg": f["ppg5"], "form5_gf": f["gf5"], "form5_ga": f["ga5"],
            "form10_ppg": f["ppg10"], "form10_gf": f["gf10"], "form10_ga": f["ga10"],
            "xg5": f["xgf5"], "xga5": f["xga5"],
            # P6 : proxys de qualité de tir (glissants) + état calendrier/lieu.
            "xg_per_shot": f["xg_per_shot"], "sot_ratio": f["sot_ratio"],
            "finishing": f["finishing"], "def_xg_per_shot": f["def_xg_per_shot"],
            "recent_dates": list(recent_dates.get(team, [])),
            "venue": last_venue.get(team),
        }
    return snap


def make_match_features(home_state: dict, away_state: dict, neutral: bool,
                        competition: str, is_club: bool,
                        ref_date: pd.Timestamp | None = None) -> dict:
    """Assemble le vecteur de features d'un match hypothétique depuis deux états."""
    ref = ref_date or pd.Timestamp.today().normalize()

    def _rest(state):
        ld = state.get("last_date")
        return float((ref - ld).days) if ld is not None else np.nan

    h_off = home_state.get("off_elo", elo.INITIAL_RATING)
    h_def = home_state.get("def_elo", elo.INITIAL_RATING)
    a_off = away_state.get("off_elo", elo.INITIAL_RATING)
    a_def = away_state.get("def_elo", elo.INITIAL_RATING)
    sv = _squad_value_feats(home_state.get("squad_value"), away_state.get("squad_value"))

    # --- Phase P6 (match futur) ---
    # Lieu du match = stade du domicile si non neutre. Météo NaN (l'archive ne
    # couvre pas l'avenir). Congestion/déplacement dérivés de l'état des équipes.
    cur_venue = None if neutral else home_state.get("venue")
    p6 = {
        "home_xg_per_shot": home_state.get("xg_per_shot", np.nan),
        "away_xg_per_shot": away_state.get("xg_per_shot", np.nan),
        "home_sot_ratio": home_state.get("sot_ratio", np.nan),
        "away_sot_ratio": away_state.get("sot_ratio", np.nan),
        "home_finishing": home_state.get("finishing", np.nan),
        "away_finishing": away_state.get("finishing", np.nan),
        "home_def_xg_per_shot": home_state.get("def_xg_per_shot", np.nan),
        "away_def_xg_per_shot": away_state.get("def_xg_per_shot", np.nan),
        "home_matches_14d": _congestion(home_state.get("recent_dates"), ref),
        "away_matches_14d": _congestion(away_state.get("recent_dates"), ref),
        "home_travel_km": _travel(home_state.get("venue"), cur_venue),
        "away_travel_km": _travel(away_state.get("venue"), cur_venue),
        **_weather_feats(None),
    }

    return {
        "neutral": int(neutral),
        "comp_importance": _importance(competition, is_club),
        "home_elo": home_state["elo"], "away_elo": away_state["elo"],
        "elo_diff": home_state["elo"] - away_state["elo"],
        "home_off_elo": h_off, "away_off_elo": a_off,
        "home_def_elo": h_def, "away_def_elo": a_def,
        "off_def_diff": (h_off - a_def) - (a_off - h_def),
        "home_advantage": elo.home_advantage_for(competition, is_club, neutral),
        **sv,
        "home_played": home_state["played"], "away_played": away_state["played"],
        "home_rest_days": _rest(home_state), "away_rest_days": _rest(away_state),
        "home_form5_ppg": home_state["form5_ppg"], "away_form5_ppg": away_state["form5_ppg"],
        "home_form5_gf": home_state["form5_gf"], "away_form5_gf": away_state["form5_gf"],
        "home_form5_ga": home_state["form5_ga"], "away_form5_ga": away_state["form5_ga"],
        "home_form10_ppg": home_state["form10_ppg"], "away_form10_ppg": away_state["form10_ppg"],
        "home_form10_gf": home_state["form10_gf"], "away_form10_gf": away_state["form10_gf"],
        "home_form10_ga": home_state["form10_ga"], "away_form10_ga": away_state["form10_ga"],
        "home_xg5": home_state["xg5"], "away_xg5": away_state["xg5"],
        "home_xga5": home_state["xga5"], "away_xga5": away_state["xga5"],
        "h2h_home_winrate": np.nan, "h2h_home_gd": np.nan,
        **p6,
    }


# --- persistance -----------------------------------------------------------
def build_and_store(conn, domain: str) -> pd.DataFrame:
    feats = build_features(conn, domain)
    if feats.empty:
        return feats
    table = f"features_{'club' if domain == config.DOMAIN_CLUB else 'intl'}"
    feats_to_store = feats.copy()
    feats_to_store["date"] = feats_to_store["date"].dt.strftime("%Y-%m-%d")
    feats_to_store.to_sql(table, conn, if_exists="replace", index=True)
    conn.commit()
    return feats


def build_all(db_path: str | None = None) -> dict:
    conn = db.connect(db_path)
    res = {}
    for domain in (config.DOMAIN_CLUB, config.DOMAIN_INTL):
        feats = build_and_store(conn, domain)
        res[domain] = len(feats)
    conn.close()
    return res


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    print(build_all())
