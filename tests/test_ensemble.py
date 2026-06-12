"""Tests de l'ensemble (mélange, matrice de score, contrat de sortie)."""

import numpy as np

from pipeline import config, ensemble
from pipeline.dixon_coles import DixonColesModel


def _toy_dc():
    return DixonColesModel(
        teams=["A", "B"], attack={"A": 0.4, "B": -0.2},
        defence={"A": -0.1, "B": 0.3}, base=0.2, home_adv=0.25, rho=-0.05,
        converged=True)


def test_blend_weight_zero_when_no_history():
    w = ensemble._blend_weight(config.DOMAIN_CLUB, 0, 0)
    assert w == 0.0
    w2 = ensemble._blend_weight(config.DOMAIN_CLUB, 50, 50)
    assert abs(w2 - ensemble._XGB_WEIGHT_CLUB) < 1e-9


def test_blend_weight_intl_lower_than_club():
    a = ensemble._blend_weight(config.DOMAIN_CLUB, 50, 50)
    b = ensemble._blend_weight(config.DOMAIN_INTL, 50, 50)
    assert b < a


def test_blend_lambdas_within_bounds_and_between():
    lam, mu = ensemble.blend_lambdas(1.0, 2.0, 3.0, 0.5, 0.5)
    assert 1.0 <= lam <= 3.0
    assert 0.5 <= mu <= 2.0


def test_score_matrix_normalised():
    mat = ensemble.score_matrix(1.4, 1.1, -0.05, max_goals=10)
    assert abs(mat.sum() - 1.0) < 1e-9
    assert np.all(mat >= 0)


def test_prediction_contract_probs_sum_to_one():
    pred = ensemble.EnsemblePredictor(_toy_dc(), None, None, config.DOMAIN_INTL)
    p = pred.predict("A", "B", neutral=False)
    s = p["p_home_win"] + p["p_draw"] + p["p_away_win"]
    assert abs(s - 1.0) < 1e-3
    for key in ["exp_home_goals", "exp_away_goals", "most_likely_score",
                "p_over_2_5", "p_btts", "score_matrix"]:
        assert key in p


def test_neutral_reduces_home_advantage():
    pred = ensemble.EnsemblePredictor(_toy_dc(), None, None, config.DOMAIN_INTL)
    home = pred.predict("A", "B", neutral=False)["p_home_win"]
    neutral = pred.predict("A", "B", neutral=True)["p_home_win"]
    assert home > neutral
