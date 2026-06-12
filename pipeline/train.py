"""Entraînement final + export des artefacts du modèle.

Une fois la performance validée par le backtest, on entraîne les modèles sur TOUTES
les données disponibles (le maximum d'information pour les prédictions futures) et on
sauvegarde tout dans data/models/ :
  - dixon_coles_{domain}.json   : paramètres Dixon-Coles (attaque/défense/base/...)
  - xgb_{domain}_home.json / _away.json : régresseurs XGBoost
  - calibrator_{domain}.json    : régression isotonique par classe
  - meta.json                   : domaine, nb de matchs, date d'entraînement

La calibration finale est ajustée sur des prédictions de backtest hors échantillon
(la dernière passe walk-forward), pas sur les données d'entraînement, pour ne pas
être trop optimiste.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date

import numpy as np

from . import backtest, config, db
from .backtest import _dc_matches, _load
from .calibration import ProbabilityCalibrator
from .dixon_coles import fit_dixon_coles
from .ensemble import EnsemblePredictor
from .xgb_model import FEATURE_COLS, XGBPoissonModel

log = logging.getLogger("pipeline.train")


def _paths(domain: str) -> dict:
    tag = "club" if domain == config.DOMAIN_CLUB else "intl"
    d = config.MODELS_DIR
    return {
        "dc": os.path.join(d, f"dixon_coles_{tag}.json"),
        "xgb_home": os.path.join(d, f"xgb_{tag}_home.json"),
        "xgb_away": os.path.join(d, f"xgb_{tag}_away.json"),
        "cal": os.path.join(d, f"calibrator_{tag}.json"),
        "meta": os.path.join(d, f"meta_{tag}.json"),
    }


def _fit_calibrator(df, domain, holdout_frac=0.25) -> ProbabilityCalibrator:
    """Ajuste la calibration sur une fenêtre finale hors échantillon."""
    n = len(df)
    split = int(n * (1 - holdout_frac))
    train, hold = df.iloc[:split], df.iloc[split:]
    if len(hold) < 200:
        return ProbabilityCalibrator()

    dc = fit_dixon_coles(_dc_matches(train), verbose=False)
    xgb = XGBPoissonModel().fit(train) if len(train) >= 300 else None
    pred = EnsemblePredictor(dc, xgb, None, domain)

    raw, out = [], []
    from . import metrics
    for r in hold.itertuples(index=False):
        feat = {c: getattr(r, c, np.nan) for c in
                FEATURE_COLS + ["home_played", "away_played"]}
        raw.append(pred.probs(r.home_team_id, r.away_team_id, bool(r.neutral), feat))
        out.append(metrics.outcome_from_goals(int(r.home_goals), int(r.away_goals)))
    return ProbabilityCalibrator().fit(np.array(raw), np.array(out))


def train_domain(conn, domain: str) -> dict:
    df = _load(conn, domain)
    if df.empty:
        log.warning("Aucune donnée pour %s", domain)
        return {}
    paths = _paths(domain)

    log.info("[%s] Dixon-Coles sur %d matchs…", domain, len(df))
    dc = fit_dixon_coles(_dc_matches(df), verbose=True)
    with open(paths["dc"], "w", encoding="utf-8") as f:
        json.dump(dc.to_dict(), f, ensure_ascii=False)

    if len(df) >= 300:
        log.info("[%s] XGBoost Poisson…", domain)
        xgb = XGBPoissonModel().fit(df)
        xgb.save(paths["xgb_home"], paths["xgb_away"])

    log.info("[%s] Calibration hors échantillon…", domain)
    cal = _fit_calibrator(df, domain)
    with open(paths["cal"], "w", encoding="utf-8") as f:
        json.dump(cal.to_dict(), f, ensure_ascii=False)

    meta = {
        "domain": domain, "n_matches": int(len(df)),
        "trained_on": str(date.today()),
        "n_teams": int(len(set(df["home_team_id"]) | set(df["away_team_id"]))),
        "has_xgb": len(df) >= 300, "calibrated": cal.fitted,
    }
    with open(paths["meta"], "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    log.info("[%s] Artefacts écrits dans %s", domain, config.MODELS_DIR)
    return meta


def load_predictor(domain: str) -> EnsemblePredictor:
    """Recharge un EnsemblePredictor depuis les artefacts sauvegardés."""
    from .dixon_coles import DixonColesModel
    paths = _paths(domain)
    with open(paths["dc"], encoding="utf-8") as f:
        dc = DixonColesModel.from_dict(json.load(f))
    xgb = None
    if os.path.exists(paths["xgb_home"]):
        xgb = XGBPoissonModel()
        xgb.load(paths["xgb_home"], paths["xgb_away"])
    with open(paths["cal"], encoding="utf-8") as f:
        cal = ProbabilityCalibrator.from_dict(json.load(f))
    return EnsemblePredictor(dc, xgb, cal, domain)


def train_all(db_path: str | None = None) -> dict:
    conn = db.connect(db_path)
    res = {}
    for domain in (config.DOMAIN_CLUB, config.DOMAIN_INTL):
        res[domain] = train_domain(conn, domain)
    conn.close()
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(train_all(), indent=2, ensure_ascii=False))
