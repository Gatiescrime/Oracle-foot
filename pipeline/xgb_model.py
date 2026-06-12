"""XGBoost à objectif Poisson : prédit séparément les buts domicile et extérieur.

Là où Dixon-Coles est linéaire dans ses paramètres (force d'attaque/défense),
XGBoost capte les interactions non linéaires entre features (forme × repos ×
niveau × importance...). On entraîne deux régresseurs Poisson :
  - l'un prédit le nombre de buts de l'équipe à domicile,
  - l'autre celui de l'équipe à l'extérieur.

La sortie de chaque modèle est un taux de buts attendu (lambda), directement
comparable à celui de Dixon-Coles -> on pourra les mélanger (blend).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import xgboost as xgb

from . import config

# Features d'entrée (toutes « avant match », sans fuite). Les cotes BRUTES restent
# exclues ; en revanche les probabilités de marché DÉVIGÉES d'ouverture peuvent être
# ajoutées de façon optionnelle (Phase P2, pré-match -> sans fuite).
FEATURE_COLS = [
    "neutral", "comp_importance", "elo_diff", "home_elo", "away_elo",
    "home_played", "away_played", "home_rest_days", "away_rest_days",
    "home_form5_ppg", "away_form5_ppg", "home_form5_gf", "away_form5_gf",
    "home_form5_ga", "away_form5_ga",
    "home_form10_ppg", "away_form10_ppg", "home_form10_gf", "away_form10_gf",
    "home_form10_ga", "away_form10_ga",
    "home_xg5", "away_xg5", "home_xga5", "away_xga5",
    "h2h_home_winrate", "h2h_home_gd",
]

# Probabilités de marché dévigées (Phase P2), ajoutées seulement si activé.
MARKET_FEATURE_COLS = ["p_mkt_home", "p_mkt_draw", "p_mkt_away"]

# Enrichissement Phase P5 (valeur d'effectif + Elo offensif/défensif + avantage
# du terrain par compétition), ajouté seulement si activé. Toutes pré-match.
P5_FEATURE_COLS = [
    "home_off_elo", "away_off_elo", "home_def_elo", "away_def_elo", "off_def_diff",
    "home_advantage",
    "home_squad_value", "away_squad_value", "squad_value_logratio",
]

# Contexte Phase P6 (qualité de tir, congestion, déplacement, météo). Liste RETENUE
# après l'évaluation walk-forward (pipeline/p6_eval.py) : seules les colonnes au gain
# prouvé contre P1 figurent ici — discipline anti-surapprentissage. Les autres
# colonnes restent calculées/stockées mais hors du modèle. Activé si P6_FEATURES_ENABLED.
#
# VERDICT P6 (eval walk-forward propre, colsample_bytree=1.0, 6 plis) : AUCUN groupe
# n'apporte un gain honnête. Détail des deltas vs baseline (P5 livré) :
#   +A_tir       : d_logloss -0.0048 mais d_rps +0.00000, d_roi -0.97 (coûte cher au pari) ;
#   +B_calendrier: négligeable (d_logloss -0.00013), d_roi -0.17 ;
#   +C_meteo     : sur over/under, d_ou_brier -0.00022 / d_ou_logloss -0.00049 (bruit,
#                  ~0,1 % relatif), mais 1X2 DÉGRADÉ (d_logloss +0.00085), d_roi -0.20 ;
#   +tout        : RÉGRESSE (d_logloss +0.0068, d_roi -1.0) — surapprentissage net.
# Pour mémoire, P5 apportait ~-0.00087 RPS : P6 est un ordre de grandeur en dessous, dans
# le bruit. On JETTE donc toutes les variables P6 du modèle (liste vide). Elles restent
# calculées/stockées (audit + ré-évaluation future), mais n'entrent pas dans XGBoost.
P6_FEATURE_COLS: list[str] = []


def active_feature_cols() -> list[str]:
    """Liste de features effective selon la config (marché P2, P5, contexte P6)."""
    cols = list(FEATURE_COLS)
    if config.MARKET_FEATURES_ENABLED:
        cols += MARKET_FEATURE_COLS
    if config.P5_FEATURES_ENABLED:
        cols += P5_FEATURE_COLS
    if config.P6_FEATURES_ENABLED:
        cols += P6_FEATURE_COLS
    return cols

_PARAMS = dict(
    objective="count:poisson",
    n_estimators=400,
    learning_rate=0.03,
    max_depth=4,
    min_child_weight=5,
    subsample=0.8,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    max_delta_step=0.7,   # recommandé pour la régression Poisson (stabilité)
    n_jobs=-1,
    random_state=42,
)


class XGBPoissonModel:
    def __init__(self, params: dict | None = None, feature_cols: list[str] | None = None):
        self.params = {**_PARAMS, **(params or {})}
        self.model_home = xgb.XGBRegressor(**self.params)
        self.model_away = xgb.XGBRegressor(**self.params)
        self.features = list(feature_cols) if feature_cols is not None else active_feature_cols()

    def _matrix(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.reindex(columns=self.features).astype(float)

    def fit(self, df: pd.DataFrame) -> "XGBPoissonModel":
        X = self._matrix(df)
        self.model_home.fit(X, df["home_goals"].to_numpy(dtype=float))
        self.model_away.fit(X, df["away_goals"].to_numpy(dtype=float))
        return self

    def predict_lambdas(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        X = self._matrix(df)
        lam = np.clip(self.model_home.predict(X), 0.05, 8.0)
        mu = np.clip(self.model_away.predict(X), 0.05, 8.0)
        return lam, mu

    def predict_one(self, feat: dict) -> tuple[float, float]:
        df = pd.DataFrame([feat])
        lam, mu = self.predict_lambdas(df)
        return float(lam[0]), float(mu[0])

    def save(self, path_home: str, path_away: str) -> None:
        self.model_home.save_model(path_home)
        self.model_away.save_model(path_away)

    def load(self, path_home: str, path_away: str) -> "XGBPoissonModel":
        self.model_home.load_model(path_home)
        self.model_away.load_model(path_away)
        return self
