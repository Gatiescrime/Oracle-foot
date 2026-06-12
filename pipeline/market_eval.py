"""Phase P2 — expérience : les cotes du marché aident-elles le modèle ?

On rejoue l'histoire (walk-forward, anti-fuite) en comparant plusieurs variantes,
toutes mesurées par la MÊME barre :
  * qualité probabiliste : RPS et log-loss hors échantillon ;
  * exploitabilité (la vérité, Phase P1) : nombre de value bets, rendement, CLV.

Variantes comparées (clubs) :
  * baseline           : le modèle actuel (sans aucune cote).
  * features           : XGBoost reçoit EN PLUS les probabilités de marché dévigées
                         (cotes d'OUVERTURE -> pré-match, sans fuite).
  * blend_w            : mélange final p = (1-w)·modèle + w·marché, pour w balayé.
  * marché (référence) : les probabilités de marché dévigées seules (w=1).

But : trouver le poids de blend qui minimise le RPS, et VÉRIFIER si un meilleur RPS
se traduit (ou non) par un edge réel au pari. On documente le delta honnêtement.
"""

from __future__ import annotations

import logging

import numpy as np

from . import betting, config, db, metrics
from .backtest import _dc_matches, _load
from .calibration import ProbabilityCalibrator
from .dixon_coles import fit_dixon_coles
from .ensemble import EnsemblePredictor, blend_with_market
from .xgb_model import FEATURE_COLS, MARKET_FEATURE_COLS, XGBPoissonModel

log = logging.getLogger("pipeline.market_eval")

_BLEND_WEIGHTS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
_LABELS = ("home", "draw", "away")


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(f) or f <= 1.0) else f


def _collect_rows(conn, domain: str, n_folds: int, min_train_frac: float) -> list[dict]:
    """Walk-forward : pour chaque match test, probas baseline/features/marché + cotes."""
    df = _load(conn, domain)
    n = len(df)
    if n < 300:
        return []
    odds = betting._odds_by_match(conn, domain)

    start = int(n * min_train_frac)
    bounds = np.linspace(start, n, n_folds + 1, dtype=int)

    hist_base: list[np.ndarray] = []
    hist_feat: list[np.ndarray] = []
    hist_out: list[int] = []
    rows: list[dict] = []
    feat_cols = FEATURE_COLS + MARKET_FEATURE_COLS

    for k in range(n_folds):
        lo, hi = bounds[k], bounds[k + 1]
        if hi <= lo:
            continue
        train, test = df.iloc[:lo], df.iloc[lo:hi]
        if len(train) < 300:
            continue

        dc = fit_dixon_coles(_dc_matches(train), verbose=False)
        xgb_base = XGBPoissonModel(feature_cols=FEATURE_COLS).fit(train)
        xgb_feat = XGBPoissonModel(feature_cols=feat_cols).fit(train)

        cal_base = (ProbabilityCalibrator().fit(np.array(hist_base), np.array(hist_out))
                    if len(hist_base) >= 300 else None)
        cal_feat = (ProbabilityCalibrator().fit(np.array(hist_feat), np.array(hist_out))
                    if len(hist_feat) >= 300 else None)
        pred_base = EnsemblePredictor(dc, xgb_base, cal_base, domain)
        pred_feat = EnsemblePredictor(dc, xgb_feat, cal_feat, domain)

        for r in test.itertuples(index=False):
            feat = {c: getattr(r, c, np.nan) for c in feat_cols + ["home_played", "away_played"]}
            home, away, neutral = r.home_team_id, r.away_team_id, bool(r.neutral)
            pb = pred_base.predict(home, away, neutral, feat)
            pf = pred_feat.predict(home, away, neutral, feat)
            vb = np.array([pb["p_home_win"], pb["p_draw"], pb["p_away_win"]])
            vf = np.array([pf["p_home_win"], pf["p_draw"], pf["p_away_win"]])
            outcome = metrics.outcome_from_goals(int(r.home_goals), int(r.away_goals))
            hist_base.append(vb); hist_feat.append(vf); hist_out.append(outcome)

            pm = np.array([getattr(r, "p_mkt_home", np.nan),
                           getattr(r, "p_mkt_draw", np.nan),
                           getattr(r, "p_mkt_away", np.nan)])
            if r.match_id not in odds.index:
                continue
            o = odds.loc[r.match_id]
            rows.append({
                "date": r.date, "league": r.competition, "outcome": outcome,
                "over": (int(r.home_goals) + int(r.away_goals)) > 2,
                "vb": vb, "vf": vf, "pm": pm,
                "pover_b": float(pb["p_over_2_5"]), "pover_f": float(pf["p_over_2_5"]),
                "dec": [_num(o.odds_home), _num(o.odds_draw), _num(o.odds_away)],
                "mx": [_num(o.odds_max_home), _num(o.odds_max_draw), _num(o.odds_max_away)],
                "cl": [_num(o.odds_close_home), _num(o.odds_close_draw), _num(o.odds_close_away)],
                "ou_dec": [_num(o.odds_open_over25), _num(o.odds_open_under25)],
                "ou_cl": [_num(o.odds_close_over25), _num(o.odds_close_under25)],
            })
    return rows


def _selections(rows: list[dict], get_1x2, get_over) -> list[dict]:
    """Construit les sélections pariables pour une variante (réutilise le moteur P1)."""
    sels = []
    for row in rows:
        v = get_1x2(row)
        for i in range(3):
            if row["dec"][i] is None:
                continue
            sels.append({
                "date": row["date"], "league": row["league"], "domain": "club",
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
                "date": row["date"], "league": row["league"], "domain": "club",
                "market": "OU25", "selection": label, "model_prob": float(p),
                "dec_odds": dec, "max_odds": dec, "close_odds": close, "won": won,
            })
    return sels


def _quality(rows, get_1x2) -> dict:
    """RPS et log-loss 1X2 hors échantillon pour une variante."""
    probs = np.array([get_1x2(r) for r in rows])
    out = np.array([r["outcome"] for r in rows])
    return {"rps": round(metrics.rps(probs, out), 5),
            "logloss": round(metrics.log_loss_multi(probs, out), 5)}


def _betting(rows, get_1x2, get_over, edge: float) -> dict:
    sels = _selections(rows, get_1x2, get_over)
    bets = betting.find_bets(sels, edge=edge)
    s = betting.simulate(bets, mode="flat")
    return {"n_bets": s["n_bets"], "yield": s["yield"], "roi": s["roi"],
            "hit_rate": s["hit_rate"], "clv_mean": s["clv_mean"],
            "clv_beat_rate": s["clv_beat_rate"]}


def _variant(rows, get_1x2, get_over, edge) -> dict:
    return {**_quality(rows, get_1x2), **_betting(rows, get_1x2, get_over, edge)}


def run(conn, domain: str = config.DOMAIN_CLUB, *, n_folds: int = 6,
        min_train_frac: float = 0.5, edge: float | None = None) -> dict:
    edge = config.BET_EDGE_THRESHOLD if edge is None else edge
    rows = _collect_rows(conn, domain, n_folds, min_train_frac)
    rows = [r for r in rows if not np.isnan(r["pm"]).any()]  # marché requis pour comparer
    if not rows:
        return {}

    res = {"domain": domain, "n_matches": len(rows), "edge_threshold": edge, "variants": {}}
    V = res["variants"]
    V["baseline"] = _variant(rows, lambda r: r["vb"], lambda r: r["pover_b"], edge)
    V["features"] = _variant(rows, lambda r: r["vf"], lambda r: r["pover_f"], edge)
    V["market_only"] = _variant(rows, lambda r: r["pm"], lambda r: r["pover_b"], edge)

    best_w, best_rps = 0.0, V["baseline"]["rps"]
    for w in _BLEND_WEIGHTS:
        name = f"blend_{w:g}"
        V[name] = _variant(
            rows, lambda r, w=w: blend_with_market(r["vb"], r["pm"], w),
            lambda r: r["pover_b"], edge)
        if V[name]["rps"] < best_rps:
            best_rps, best_w = V[name]["rps"], w
    res["best_blend_weight"] = best_w
    res["best_blend_rps"] = best_rps
    return res


def run_all(db_path: str | None = None, edge: float | None = None) -> dict:
    conn = db.connect(db_path)
    out = run(conn, config.DOMAIN_CLUB, edge=edge)
    conn.close()
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import json
    print(json.dumps(run_all(), indent=2, ensure_ascii=False))
