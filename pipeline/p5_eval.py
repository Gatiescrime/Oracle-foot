"""Phase P5 — expérience : l'enrichissement aide-t-il le modèle ?

On compare honnêtement, par walk-forward anti-fuite, DEUX configurations sur la
MÊME barre que les phases précédentes (RPS, log-loss, puis ROI/CLV au pari) :

  * baseline : le modèle livré (features historiques, avantage du terrain constant) ;
  * p5       : EN PLUS la valeur d'effectif, l'Elo offensif/défensif, et l'avantage
               du terrain SPÉCIFIQUE à la compétition (qui modifie aussi l'Elo).

Pour rendre la comparaison juste, on reconstruit les features DEUX fois (drapeau
P5 OFF puis ON) : ainsi la variante p5 reflète l'intégralité du changement, y
compris l'avantage du terrain par compétition dans le calcul Elo lui-même.

But : mesurer le delta (gain ou neutre) sans tricher, et le documenter tel quel.
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

log = logging.getLogger("pipeline.p5_eval")

_LABELS = ("home", "draw", "away")


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(f) or f <= 1.0) else f


def _build(conn, domain: str, p5_on: bool) -> pd.DataFrame:
    """Reconstruit la table de features en mémoire avec le drapeau P5 forcé."""
    old = config.P5_FEATURES_ENABLED
    config.P5_FEATURES_ENABLED = p5_on
    try:
        feats = features.build_features(conn, domain)
    finally:
        config.P5_FEATURES_ENABLED = old
    df = feats.reset_index()
    df = df.dropna(subset=["home_goals", "away_goals"]).sort_values(
        ["date", "match_id"], kind="mergesort").reset_index(drop=True)
    return df


def _collect_rows(conn, domain: str, n_folds: int, min_train_frac: float) -> list[dict]:
    """Walk-forward : probas baseline vs p5 hors échantillon + cotes pour le pari."""
    df_base = _build(conn, domain, p5_on=False)
    df_p5 = _build(conn, domain, p5_on=True)
    # Les deux vues partagent l'ordre (mêmes matchs, même tri) -> alignées par position.
    if len(df_base) != len(df_p5) or len(df_base) < 300:
        return []
    odds = betting._odds_by_match(conn, domain)

    n = len(df_base)
    start = int(n * min_train_frac)
    bounds = np.linspace(start, n, n_folds + 1, dtype=int)

    p5_cols = FEATURE_COLS + P5_FEATURE_COLS
    hist_base: list[np.ndarray] = []
    hist_p5: list[np.ndarray] = []
    hist_out: list[int] = []
    rows: list[dict] = []

    for k in range(n_folds):
        lo, hi = bounds[k], bounds[k + 1]
        if hi <= lo:
            continue
        tr_b, te_b = df_base.iloc[:lo], df_base.iloc[lo:hi]
        tr_p, te_p = df_p5.iloc[:lo], df_p5.iloc[lo:hi]
        if len(tr_b) < 300:
            continue

        dc_b = fit_dixon_coles(_dc_matches(tr_b), verbose=False)
        dc_p = fit_dixon_coles(_dc_matches(tr_p), verbose=False)
        xgb_b = XGBPoissonModel(feature_cols=FEATURE_COLS).fit(tr_b)
        xgb_p = XGBPoissonModel(feature_cols=p5_cols).fit(tr_p)

        cal_b = (ProbabilityCalibrator().fit(np.array(hist_base), np.array(hist_out))
                 if len(hist_base) >= 300 else None)
        cal_p = (ProbabilityCalibrator().fit(np.array(hist_p5), np.array(hist_out))
                 if len(hist_p5) >= 300 else None)
        pred_b = EnsemblePredictor(dc_b, xgb_b, cal_b, domain)
        pred_p = EnsemblePredictor(dc_p, xgb_p, cal_p, domain)

        for rb, rp in zip(te_b.itertuples(index=False), te_p.itertuples(index=False)):
            home, away, neutral = rb.home_team_id, rb.away_team_id, bool(rb.neutral)
            fb = {c: getattr(rb, c, np.nan) for c in FEATURE_COLS + ["home_played", "away_played"]}
            fp = {c: getattr(rp, c, np.nan) for c in p5_cols + ["home_played", "away_played"]}
            pb = pred_b.predict(home, away, neutral, fb)
            pp = pred_p.predict(home, away, neutral, fp)
            vb = np.array([pb["p_home_win"], pb["p_draw"], pb["p_away_win"]])
            vp = np.array([pp["p_home_win"], pp["p_draw"], pp["p_away_win"]])
            outcome = metrics.outcome_from_goals(int(rb.home_goals), int(rb.away_goals))
            hist_base.append(vb); hist_p5.append(vp); hist_out.append(outcome)

            if rb.match_id not in odds.index:
                continue
            o = odds.loc[rb.match_id]
            rows.append({
                "date": rb.date, "league": rb.competition, "outcome": outcome,
                "over": (int(rb.home_goals) + int(rb.away_goals)) > 2,
                "vb": vb, "vp": vp,
                "pover_b": float(pb["p_over_2_5"]), "pover_p": float(pp["p_over_2_5"]),
                "dec": [_num(o.odds_home), _num(o.odds_draw), _num(o.odds_away)],
                "mx": [_num(o.odds_max_home), _num(o.odds_max_draw), _num(o.odds_max_away)],
                "cl": [_num(o.odds_close_home), _num(o.odds_close_draw), _num(o.odds_close_away)],
                "ou_dec": [_num(o.odds_open_over25), _num(o.odds_open_under25)],
                "ou_cl": [_num(o.odds_close_over25), _num(o.odds_close_under25)],
            })
    return rows


def _selections(rows: list[dict], get_1x2, get_over, domain: str) -> list[dict]:
    sels = []
    for row in rows:
        v = get_1x2(row)
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
        p_over = get_over(row)
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


def _quality(rows, get_1x2) -> dict:
    probs = np.array([get_1x2(r) for r in rows])
    out = np.array([r["outcome"] for r in rows])
    return {"rps": round(metrics.rps(probs, out), 5),
            "logloss": round(metrics.log_loss_multi(probs, out), 5)}


def _betting(rows, get_1x2, get_over, edge, domain) -> dict:
    sels = _selections(rows, get_1x2, get_over, domain)
    bets = betting.find_bets(sels, edge=edge)
    s = betting.simulate(bets, mode="flat")
    return {"n_bets": s["n_bets"], "yield": s["yield"], "roi": s["roi"],
            "hit_rate": s["hit_rate"], "clv_mean": s["clv_mean"],
            "clv_beat_rate": s["clv_beat_rate"]}


def _variant(rows, get_1x2, get_over, edge, domain) -> dict:
    return {**_quality(rows, get_1x2), **_betting(rows, get_1x2, get_over, edge, domain)}


def run(conn, domain: str = config.DOMAIN_CLUB, *, n_folds: int = 6,
        min_train_frac: float = 0.5, edge: float | None = None) -> dict:
    edge = config.BET_EDGE_THRESHOLD if edge is None else edge
    rows = _collect_rows(conn, domain, n_folds, min_train_frac)
    if not rows:
        return {}

    res = {"domain": domain, "n_matches": len(rows), "edge_threshold": edge, "variants": {}}
    V = res["variants"]
    V["baseline"] = _variant(rows, lambda r: r["vb"], lambda r: r["pover_b"], edge, domain)
    V["p5"] = _variant(rows, lambda r: r["vp"], lambda r: r["pover_p"], edge, domain)
    res["delta_rps"] = round(V["p5"]["rps"] - V["baseline"]["rps"], 5)
    res["delta_logloss"] = round(V["p5"]["logloss"] - V["baseline"]["logloss"], 5)
    res["delta_roi"] = round(V["p5"]["roi"] - V["baseline"]["roi"], 5)
    res["verdict"] = "gain" if res["delta_rps"] < -0.0005 else (
        "régression" if res["delta_rps"] > 0.0005 else "neutre")
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
