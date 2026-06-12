"""Ensemble : mélange Dixon-Coles + XGBoost, matrice de score, calibration.

Idée directrice (A.2) : Dixon-Coles et XGBoost prédisent tous deux un couple de
taux de buts attendus (lambda_dom, lambda_ext). On les MÉLANGE, puis on reconstruit
la loi jointe des scores par un Poisson bivarié corrigé Dixon-Coles (terme tau),
d'où l'on tire toutes les sorties du contrat (1/X/2, score le plus probable, matrice
des scores, over/under 2.5, BTTS). Enfin on CALIBRE les probabilités 1/X/2.

Poids du mélange :
  - Sur les CLUBS, les données sont riches et régulières -> on fait davantage
    confiance à XGBoost (capte les interactions non linéaires).
  - Sur les SÉLECTIONS, les données sont rares et bruitées -> on s'appuie davantage
    sur Dixon-Coles (paramétrique, robuste, peu de variance).
Le poids n'est pas binaire : il dépend aussi de la richesse de données du match
précis (nombre de matchs déjà joués par les deux équipes).
"""

from __future__ import annotations

import numpy as np
from scipy.stats import poisson

from . import config
from .calibration import ProbabilityCalibrator
from .dixon_coles import DixonColesModel, _matrix_to_prediction, tau
from .xgb_model import XGBPoissonModel

# Poids de base accordé à XGBoost selon le domaine (le reste va à Dixon-Coles).
_XGB_WEIGHT_CLUB = 0.6
_XGB_WEIGHT_INTL = 0.3

# Nombre de matchs joués (min des deux équipes) au-delà duquel on accorde le poids
# XGBoost plein ; en dessous on rétrécit vers Dixon-Coles (XGBoost a peu de signal).
_RICHNESS_FULL = 10


def _blend_weight(domain: str, home_played: float, away_played: float) -> float:
    """Poids accordé à XGBoost dans [0, base]. Réduit si peu de matchs joués."""
    base = _XGB_WEIGHT_CLUB if domain == config.DOMAIN_CLUB else _XGB_WEIGHT_INTL
    played = min(_safe(home_played, 0.0), _safe(away_played, 0.0))
    richness = float(np.clip(played / _RICHNESS_FULL, 0.0, 1.0))
    return base * richness


def _safe(v, default):
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if np.isnan(f) else f


def blend_lambdas(dc_lam, dc_mu, xgb_lam, xgb_mu, weight) -> tuple[float, float]:
    """Mélange géométrique des taux de buts (stable, reste positif)."""
    w = float(np.clip(weight, 0.0, 1.0))
    lam = (dc_lam ** (1 - w)) * (xgb_lam ** w)
    mu = (dc_mu ** (1 - w)) * (xgb_mu ** w)
    return float(np.clip(lam, 0.05, 8.0)), float(np.clip(mu, 0.05, 8.0))


def blend_with_market(probs: np.ndarray, market: np.ndarray | None,
                      weight: float) -> np.ndarray:
    """Mélange convexe modèle/marché : p = (1-w)·modèle + w·marché (renormalisé).

    w=0 -> modèle pur ; w=1 -> marché pur. Sans cotes de marché (None) ou poids nul,
    renvoie les probabilités du modèle inchangées (Phase P2).
    """
    w = float(np.clip(weight, 0.0, 1.0))
    if market is None or w <= 0.0:
        return probs
    mixed = (1.0 - w) * np.asarray(probs) + w * np.asarray(market)
    s = mixed.sum()
    return mixed / s if s > 0 else probs


def score_matrix(lam: float, mu: float, rho: float, max_goals: int = 10) -> np.ndarray:
    """Loi jointe des scores (Poisson bivarié + correction Dixon-Coles)."""
    i = np.arange(0, max_goals + 1)
    mat = np.outer(poisson.pmf(i, lam), poisson.pmf(i, mu))
    for x in (0, 1):
        for y in (0, 1):
            mat[x, y] *= float(tau(x, y, lam, mu, rho))
    mat /= mat.sum()
    return mat


class EnsemblePredictor:
    """Combine Dixon-Coles, XGBoost et la calibration en un seul prédicteur."""

    def __init__(self, dc: DixonColesModel, xgb: XGBPoissonModel | None,
                 calibrator: ProbabilityCalibrator | None, domain: str):
        self.dc = dc
        self.xgb = xgb
        self.calibrator = calibrator or ProbabilityCalibrator()
        self.domain = domain

    def predict_lambdas(self, home: str, away: str, neutral: bool,
                        feat: dict | None) -> tuple[float, float, float]:
        """Renvoie (lambda_dom, lambda_ext, poids_xgb) après mélange."""
        dc_lam, dc_mu = self.dc.predict_lambdas(home, away, neutral)
        if self.xgb is None or feat is None:
            return dc_lam, dc_mu, 0.0
        xgb_lam, xgb_mu = self.xgb.predict_one(feat)
        w = _blend_weight(self.domain,
                          feat.get("home_played"), feat.get("away_played"))
        lam, mu = blend_lambdas(dc_lam, dc_mu, xgb_lam, xgb_mu, w)
        return lam, mu, w

    def predict(self, home: str, away: str, neutral: bool = False,
                feat: dict | None = None, max_goals: int = 10,
                adjustment: dict | None = None,
                market_probs: np.ndarray | None = None,
                blend_weight: float | None = None) -> dict:
        lam, mu, w = self.predict_lambdas(home, away, neutral, feat)

        # Ajustement qualitatif OPTIONNEL et BORNÉ (Phase 5) : multiplie les buts
        # attendus avant de reconstruire la matrice. Sans ajustement -> identique.
        if adjustment is not None:
            lam = float(np.clip(lam * adjustment.get("mult_dom", 1.0), 0.05, 8.0))
            mu = float(np.clip(mu * adjustment.get("mult_ext", 1.0), 0.05, 8.0))

        mat = score_matrix(lam, mu, self.dc.rho, max_goals=max_goals)
        pred = _matrix_to_prediction(mat, home, away, neutral, (lam, mu))

        # Calibration des trois probabilités 1/X/2 (renormalisées).
        ph, pd_, pa = self.calibrator.transform_one(
            pred["p_home_win"], pred["p_draw"], pred["p_away_win"])

        # Mélange optionnel avec le marché (Phase P2) : APRÈS calibration. Le poids
        # vient de l'argument explicite, sinon de la config (par défaut 0 -> no-op).
        bw = config.MARKET_BLEND_WEIGHT if blend_weight is None else blend_weight
        ph, pd_, pa = blend_with_market(np.array([ph, pd_, pa]), market_probs, bw)

        pred["p_home_win"] = round(float(ph), 4)
        pred["p_draw"] = round(float(pd_), 4)
        pred["p_away_win"] = round(float(pa), 4)
        pred["blend_xgb_weight"] = round(w, 3)
        pred["score_matrix"] = np.round(mat[:7, :7], 4).tolist()
        if adjustment is not None:
            pred["qualitative"] = adjustment
        return pred

    def probs(self, home: str, away: str, neutral: bool,
              feat: dict | None) -> np.ndarray:
        """Vecteur [p_dom, p_nul, p_ext] calibré (pour le backtest)."""
        p = self.predict(home, away, neutral, feat)
        return np.array([p["p_home_win"], p["p_draw"], p["p_away_win"]])
