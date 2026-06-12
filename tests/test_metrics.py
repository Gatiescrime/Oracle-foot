"""Tests des métriques d'évaluation (RPS, dévig, calibration)."""

import numpy as np

from pipeline import metrics


def test_rps_perfect_is_zero():
    probs = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    outcomes = np.array([0, 2])
    assert metrics.rps(probs, outcomes) == 0.0


def test_rps_orders_penalise_distance():
    # se tromper de l'extrême (annoncer ext alors que dom gagne) coûte plus cher
    # que se tromper du voisin (annoncer nul).
    outcome = np.array([0])
    far = metrics.rps(np.array([[0.0, 0.0, 1.0]]), outcome)
    near = metrics.rps(np.array([[0.0, 1.0, 0.0]]), outcome)
    assert far > near


def test_rps_uniform_known_value():
    # pour une issue domicile, proba uniforme (1/3,1/3,1/3) -> RPS = 5/18.
    val = metrics.rps(np.array([[1 / 3, 1 / 3, 1 / 3]]), np.array([0]))
    assert abs(val - 5 / 18) < 1e-9


def test_implied_probs_devig_sums_to_one():
    p = metrics.implied_probs(2.0, 3.5, 4.0)
    assert p is not None
    assert abs(p.sum() - 1.0) < 1e-12
    assert np.all(p > 0)


def test_implied_probs_rejects_bad_odds():
    assert metrics.implied_probs(1.0, 3.0, 4.0) is None      # cote = 1
    assert metrics.implied_probs(np.nan, 3.0, 4.0) is None


def test_calibration_table_recovers_frequency():
    # un modèle parfaitement calibré : proba p -> fréquence p.
    rng = np.random.default_rng(0)
    n = 20000
    p_home = rng.uniform(0, 1, n)
    rest = (1 - p_home)
    probs = np.column_stack([p_home, rest / 2, rest / 2])
    draws = rng.uniform(0, 1, n)
    outcomes = np.where(draws < p_home, 0, np.where(draws < p_home + rest / 2, 1, 2))
    table = metrics.calibration_table(probs, outcomes, n_bins=10)
    for row in table:
        if row["effectif"] > 200:
            assert abs(row["predit_moyen"] - row["observe"]) < 0.05
