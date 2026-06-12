"""Backtest chronologique : la seule mesure de succès qui compte.

On rejoue l'histoire dans l'ordre du temps (walk-forward) : à chaque étape on
n'entraîne QUE sur le passé et on prédit le bloc suivant, jamais vu. On accumule
les prédictions hors échantillon, puis on calcule :
  - le RPS du modèle (plus bas = mieux), comparé au RPS du BOOKMAKER (l'étalon) ;
  - le log-loss et la courbe de calibration ;
  - une simulation de paris « à la valeur » (value betting) pour vérifier qu'on ne
    perd pas d'argent face aux cotes.

Le bookmaker est l'adversaire à battre : ses cotes dévigées donnent une probabilité
de référence. Si notre RPS n'est pas ≤ au sien, le modèle n'apporte rien d'exploitable.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import config, db, metrics
from .calibration import ProbabilityCalibrator
from .dixon_coles import fit_dixon_coles
from .ensemble import EnsemblePredictor
from .xgb_model import FEATURE_COLS, XGBPoissonModel

log = logging.getLogger("pipeline.backtest")

VALUE_EDGE = 0.05   # on parie si proba_modèle * cote - 1 > 5 %
VALUE_STAKE = 1.0   # mise plate


def _load(conn, domain: str) -> pd.DataFrame:
    table = f"features_{'club' if domain == config.DOMAIN_CLUB else 'intl'}"
    df = pd.read_sql_query(f"SELECT * FROM {table}", conn, parse_dates=["date"])
    df = df.dropna(subset=["home_goals", "away_goals"]).sort_values(
        ["date", "match_id"], kind="mergesort").reset_index(drop=True)
    return df


def _dc_matches(df: pd.DataFrame) -> pd.DataFrame:
    """Vue 'matches' attendue par Dixon-Coles (équipes = identifiants)."""
    return pd.DataFrame({
        "date": df["date"], "competition": df["competition"],
        "home_team": df["home_team_id"], "away_team": df["away_team_id"],
        "home_goals": df["home_goals"], "away_goals": df["away_goals"],
        "neutral": df["neutral"],
    })


def run_backtest(conn, domain: str, n_folds: int = 6,
                 min_train_frac: float = 0.5) -> dict:
    df = _load(conn, domain)
    n = len(df)
    if n < 200:
        log.warning("Backtest %s : trop peu de matchs (%d)", domain, n)
        return {}

    start = int(n * min_train_frac)
    bounds = np.linspace(start, n, n_folds + 1, dtype=int)

    raw_all, cal_all, out_all = [], [], []
    book_probs, book_mask = [], []
    # historique (probas brutes + issues) pour ajuster la calibration hors échantillon
    hist_raw: list[np.ndarray] = []
    hist_out: list[int] = []

    for k in range(n_folds):
        lo, hi = bounds[k], bounds[k + 1]
        if hi <= lo:
            continue
        train = df.iloc[:lo]
        test = df.iloc[lo:hi]

        dc = fit_dixon_coles(_dc_matches(train), verbose=False)
        xgb = XGBPoissonModel().fit(train) if len(train) >= 300 else None

        calibrator = None
        if len(hist_raw) >= 300:
            calibrator = ProbabilityCalibrator().fit(
                np.array(hist_raw), np.array(hist_out))

        raw_pred = EnsemblePredictor(dc, xgb, None, domain)

        for r in test.itertuples(index=False):
            feat = {c: getattr(r, c, np.nan) for c in
                    FEATURE_COLS + ["home_played", "away_played"]}
            home, away = r.home_team_id, r.away_team_id
            neutral = bool(r.neutral)
            raw = raw_pred.probs(home, away, neutral, feat)
            cal = calibrator.transform(raw.reshape(1, 3))[0] if calibrator else raw
            outcome = metrics.outcome_from_goals(int(r.home_goals), int(r.away_goals))

            raw_all.append(raw)
            cal_all.append(cal)
            out_all.append(outcome)
            hist_raw.append(raw)
            hist_out.append(outcome)

            bp = metrics.implied_probs(r.odds_home, r.odds_draw, r.odds_away)
            if bp is None:
                book_probs.append([np.nan] * 3)
                book_mask.append(False)
            else:
                book_probs.append(bp.tolist())
                book_mask.append(True)

    return _summarize(domain, np.array(raw_all), np.array(cal_all),
                      np.array(out_all), np.array(book_probs),
                      np.array(book_mask), df.iloc[bounds[0]:bounds[-1]])


def _summarize(domain, raw, cal, out, book, mask, test_df) -> dict:
    res = {
        "domain": domain,
        "n_predictions": int(len(out)),
        "rps_raw": metrics.rps(raw, out),
        "rps_calibrated": metrics.rps(cal, out),
        "logloss_raw": metrics.log_loss_multi(raw, out),
        "logloss_calibrated": metrics.log_loss_multi(cal, out),
        "calibration": metrics.calibration_table(cal, out),
    }
    m = mask.astype(bool)
    if m.sum() > 0:
        res["n_with_odds"] = int(m.sum())
        res["rps_bookmaker"] = metrics.rps(book[m], out[m])
        res["rps_model_on_odds_subset"] = metrics.rps(cal[m], out[m])
        res["value_betting"] = _value_betting(cal[m], out[m], book[m], test_df, mask)
    return res


def _value_betting(probs, outcomes, book, test_df, full_mask) -> dict:
    """Simule des paris à la valeur sur le sous-ensemble avec cotes."""
    odds = 1.0 / book   # cote dévigée équivalente (approximation prudente)
    # On reconstruit les vraies cotes brutes depuis le test_df masqué.
    sub = test_df.iloc[np.where(full_mask)[0]]
    real_odds = sub[["odds_home", "odds_draw", "odds_away"]].to_numpy(dtype=float)

    edge = probs * real_odds - 1.0
    bets = edge > VALUE_EDGE
    n_bets = int(bets.sum())
    if n_bets == 0:
        return {"n_bets": 0, "roi": 0.0, "profit": 0.0, "staked": 0.0}

    won = np.zeros_like(real_odds, dtype=bool)
    won[np.arange(len(outcomes)), outcomes] = True
    profit = np.where(bets & won, (real_odds - 1.0) * VALUE_STAKE,
                      np.where(bets, -VALUE_STAKE, 0.0)).sum()
    staked = n_bets * VALUE_STAKE
    return {
        "n_bets": n_bets,
        "staked": round(float(staked), 1),
        "profit": round(float(profit), 2),
        "roi": round(float(profit / staked), 4),
    }


def run_all(db_path: str | None = None) -> dict:
    conn = db.connect(db_path)
    res = {}
    for domain in (config.DOMAIN_CLUB, config.DOMAIN_INTL):
        log.info("Backtest %s…", domain)
        res[domain] = run_backtest(conn, domain)
    conn.close()
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import json
    print(json.dumps(run_all(), indent=2, ensure_ascii=False))
