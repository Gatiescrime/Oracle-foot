"""Phase P6 — expérience : quelles variables de contexte aident vraiment ?

On mesure honnêtement, par walk-forward anti-fuite, l'apport INCRÉMENTAL des
features de contexte P6 par-dessus le modèle courant (P5 activé), groupe par groupe :

  * baseline : features historiques + enrichissement P5 (le modèle livré) ;
  * +A_tir   : EN PLUS les proxys de qualité de tir (xG/tir, précision, finition,
               qualité des tirs concédés) ;
  * +B_calendrier : EN PLUS la congestion (matchs sur 14 j) et la distance de
               déplacement ;
  * +C_meteo : EN PLUS la météo (température, pluie, vent), surtout visée pour
               le marché over/under ;
  * +tout    : tous les groupes ensemble.

Différence avec P5 : les colonnes P6 sont PUREMENT ADDITIVES (elles ne modifient pas
le calcul Elo). On reconstruit donc les features UNE seule fois (toutes colonnes
présentes) et chaque variante n'est qu'un sous-ENSEMBLE de colonnes données à XGBoost
-> comparaison exacte et rapide, sur les MÊMES plis.

On juge sur deux barres : RPS/log-loss du 1X2, ET Brier/log-loss du over/under 2,5
(là où la météo est censée peser). Discipline anti-surapprentissage : on ne RETIENDRA
dans le modèle (xgb_model.P6_FEATURE_COLS) que les groupes au gain prouvé.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import betting, config, db, features, metrics
from .backtest import _dc_matches
from .calibration import ProbabilityCalibrator
from .dixon_coles import fit_dixon_coles
from .ensemble import EnsemblePredictor
from .xgb_model import FEATURE_COLS, P5_FEATURE_COLS, XGBPoissonModel

log = logging.getLogger("pipeline.p6_eval")

_LABELS = ("home", "draw", "away")

# Groupes de features candidates (doivent exister dans la table de features).
A_SHOTS = ["home_xg_per_shot", "away_xg_per_shot", "home_sot_ratio", "away_sot_ratio",
           "home_finishing", "away_finishing",
           "home_def_xg_per_shot", "away_def_xg_per_shot"]
B_CALENDAR = ["home_matches_14d", "away_matches_14d", "home_travel_km", "away_travel_km"]
C_WEATHER = ["temp_c", "precip_mm", "wind_kmh"]

_BASE = FEATURE_COLS + P5_FEATURE_COLS

# Mesure PROPRE : on désactive l'échantillonnage de COLONNES (colsample_bytree=1.0)
# pendant l'expérience. Sinon, ajouter des colonnes (même vides) change le sous-
# ensemble de features vu par chaque arbre -> un bruit qui masque le vrai signal.
# Avec 1.0, une variante n'ajoutant que des colonnes inertes donne un delta EXACT
# de zéro : on isole ainsi l'apport réel de chaque groupe. (Le sous-échantillonnage
# de lignes, lui, est identique entre variantes -> sans effet sur la comparaison.)
_EVAL_PARAMS = {"colsample_bytree": 1.0}


def _variant_cols() -> dict[str, list[str]]:
    return {
        "baseline": list(_BASE),
        "+A_tir": _BASE + A_SHOTS,
        "+B_calendrier": _BASE + B_CALENDAR,
        "+C_meteo": _BASE + C_WEATHER,
        "+tout": _BASE + A_SHOTS + B_CALENDAR + C_WEATHER,
    }


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(f) or f <= 1.0) else f


def _build(conn, domain: str) -> pd.DataFrame:
    """Construit les features UNE fois (P5 ON, toutes colonnes P6 présentes)."""
    old = config.P5_FEATURES_ENABLED
    config.P5_FEATURES_ENABLED = True
    try:
        feats = features.build_features(conn, domain)
    finally:
        config.P5_FEATURES_ENABLED = old
    df = feats.reset_index()
    df = df.dropna(subset=["home_goals", "away_goals"]).sort_values(
        ["date", "match_id"], kind="mergesort").reset_index(drop=True)
    return df


def _collect_rows(conn, domain: str, n_folds: int, min_train_frac: float) -> list[dict]:
    """Walk-forward : probas hors échantillon par variante + cotes pour le pari."""
    df = _build(conn, domain)
    if len(df) < 300:
        return []
    odds = betting._odds_by_match(conn, domain)
    variants = _variant_cols()

    n = len(df)
    start = int(n * min_train_frac)
    bounds = np.linspace(start, n, n_folds + 1, dtype=int)

    # Historique pour la calibration, par variante (probas DC+XGB brutes accumulées).
    hist_p: dict[str, list[np.ndarray]] = {k: [] for k in variants}
    hist_out: list[int] = []
    rows: list[dict] = []

    for k in range(n_folds):
        lo, hi = bounds[k], bounds[k + 1]
        if hi <= lo:
            continue
        tr, te = df.iloc[:lo], df.iloc[lo:hi]
        if len(tr) < 300:
            continue

        # Dixon-Coles : identique pour toutes les variantes (ne dépend que des buts).
        dc = fit_dixon_coles(_dc_matches(tr), verbose=False)
        # Un XGBoost par variante (sous-ensemble de colonnes).
        xgbs = {name: XGBPoissonModel(params=_EVAL_PARAMS, feature_cols=cols).fit(tr)
                for name, cols in variants.items()}
        cals = {name: (ProbabilityCalibrator().fit(np.array(hist_p[name]), np.array(hist_out))
                       if len(hist_p[name]) >= 300 else None)
                for name in variants}
        preds = {name: EnsemblePredictor(dc, xgbs[name], cals[name], domain)
                 for name in variants}

        for r in te.itertuples(index=False):
            home, away, neutral = r.home_team_id, r.away_team_id, bool(r.neutral)
            outcome = metrics.outcome_from_goals(int(r.home_goals), int(r.away_goals))
            row = {"date": r.date, "league": r.competition, "outcome": outcome,
                   "over": (int(r.home_goals) + int(r.away_goals)) > 2,
                   "v": {}, "pover": {}}
            for name, cols in variants.items():
                feat = {c: getattr(r, c, np.nan) for c in cols + ["home_played", "away_played"]}
                p = preds[name].predict(home, away, neutral, feat)
                v = np.array([p["p_home_win"], p["p_draw"], p["p_away_win"]])
                row["v"][name] = v
                row["pover"][name] = float(p["p_over_2_5"])
                hist_p[name].append(v)
            hist_out.append(outcome)

            if r.match_id not in odds.index:
                continue
            o = odds.loc[r.match_id]
            row.update({
                "dec": [_num(o.odds_home), _num(o.odds_draw), _num(o.odds_away)],
                "mx": [_num(o.odds_max_home), _num(o.odds_max_draw), _num(o.odds_max_away)],
                "cl": [_num(o.odds_close_home), _num(o.odds_close_draw), _num(o.odds_close_away)],
                "ou_dec": [_num(o.odds_open_over25), _num(o.odds_open_under25)],
                "ou_cl": [_num(o.odds_close_over25), _num(o.odds_close_under25)],
            })
            rows.append(row)
    return rows


def _quality_1x2(rows, name) -> dict:
    probs = np.array([r["v"][name] for r in rows])
    out = np.array([r["outcome"] for r in rows])
    return {"rps": round(metrics.rps(probs, out), 5),
            "logloss": round(metrics.log_loss_multi(probs, out), 5)}


def _quality_ou(rows, name) -> dict:
    """Brier + log-loss binaires sur le over 2,5 (où la météo est censée peser)."""
    p = np.clip(np.array([r["pover"][name] for r in rows]), 1e-6, 1 - 1e-6)
    y = np.array([1.0 if r["over"] else 0.0 for r in rows])
    brier = float(np.mean((p - y) ** 2))
    logloss = float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))))
    return {"ou_brier": round(brier, 5), "ou_logloss": round(logloss, 5)}


def _selections(rows, name, domain) -> list[dict]:
    sels = []
    for row in rows:
        if "dec" not in row:
            continue
        v = row["v"][name]
        for i in range(3):
            if row["dec"][i] is None:
                continue
            sels.append({
                "date": row["date"], "league": row["league"], "domain": domain,
                "market": "1X2", "selection": _LABELS[i], "model_prob": float(v[i]),
                "dec_odds": row["dec"][i],
                "max_odds": row["mx"][i] if row["mx"][i] else row["dec"][i],
                "close_odds": row["cl"][i], "won": row["outcome"] == i,
            })
        p_over = row["pover"][name]
        for label, p, dec, close, won in (
            ("over", p_over, row["ou_dec"][0], row["ou_cl"][0], row["over"]),
            ("under", 1.0 - p_over, row["ou_dec"][1], row["ou_cl"][1], not row["over"]),
        ):
            if dec is None:
                continue
            sels.append({
                "date": row["date"], "league": row["league"], "domain": domain,
                "market": "OU25", "selection": label, "model_prob": float(p),
                "dec_odds": dec, "max_odds": dec, "close_odds": close, "won": won,
            })
    return sels


def _betting(rows, name, edge, domain) -> dict:
    bets = betting.find_bets(_selections(rows, name, domain), edge=edge)
    s = betting.simulate(bets, mode="flat")
    return {"n_bets": s["n_bets"], "roi": s["roi"], "clv_mean": s["clv_mean"]}


def run(conn, domain: str = config.DOMAIN_CLUB, *, n_folds: int = 6,
        min_train_frac: float = 0.5, edge: float | None = None) -> dict:
    edge = config.BET_EDGE_THRESHOLD if edge is None else edge
    rows = _collect_rows(conn, domain, n_folds, min_train_frac)
    if not rows:
        return {}

    variants = list(_variant_cols())
    res = {"domain": domain, "n_matches": len(rows), "edge_threshold": edge,
           "variants": {}}
    for name in variants:
        res["variants"][name] = {**_quality_1x2(rows, name), **_quality_ou(rows, name),
                                 **_betting(rows, name, edge, domain)}

    base = res["variants"]["baseline"]
    res["deltas"] = {}
    for name in variants:
        if name == "baseline":
            continue
        v = res["variants"][name]
        res["deltas"][name] = {
            "d_rps": round(v["rps"] - base["rps"], 5),
            "d_logloss": round(v["logloss"] - base["logloss"], 5),
            "d_ou_brier": round(v["ou_brier"] - base["ou_brier"], 5),
            "d_ou_logloss": round(v["ou_logloss"] - base["ou_logloss"], 5),
            "d_roi": round(v["roi"] - base["roi"], 5),
        }
    return res


def run_all(db_path: str | None = None, edge: float | None = None) -> dict:
    conn = db.connect(db_path)
    out = {d: run(conn, d, edge=edge) for d in (config.DOMAIN_CLUB, config.DOMAIN_INTL)}
    conn.close()
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import json
    print(json.dumps(run_all(), indent=2, ensure_ascii=False))
