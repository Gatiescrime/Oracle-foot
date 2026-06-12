"""Métriques d'évaluation : RPS, probabilités implicites des cotes, calibration.

RPS (Ranked Probability Score) : la métrique de référence pour les prédictions
1/X/2, car elle tient compte de l'ORDRE des issues (se tromper en donnant la
victoire extérieure quand c'est une victoire domicile coûte plus cher qu'un nul).
Plus le RPS est BAS, mieux c'est. Le RPS d'un bookmaker sert d'étalon.
"""

from __future__ import annotations

import numpy as np

OUTCOME_HOME, OUTCOME_DRAW, OUTCOME_AWAY = 0, 1, 2


def outcome_from_goals(hg: int, ag: int) -> int:
    return OUTCOME_HOME if hg > ag else (OUTCOME_DRAW if hg == ag else OUTCOME_AWAY)


def rps(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """RPS moyen. probs (N,3) [dom,nul,ext], outcomes (N,) dans {0,1,2}."""
    probs = np.asarray(probs, dtype=float).reshape(-1, 3)
    outcomes = np.asarray(outcomes, dtype=int)
    obs = np.zeros_like(probs)
    obs[np.arange(len(outcomes)), outcomes] = 1.0
    cp = np.cumsum(probs, axis=1)
    co = np.cumsum(obs, axis=1)
    # somme sur les K-1 premiers seuils (le dernier cumul vaut 1 des deux côtés)
    return float(np.mean(np.sum((cp[:, :2] - co[:, :2]) ** 2, axis=1) / 2.0))


def implied_probs(odds_home, odds_draw, odds_away) -> np.ndarray | None:
    """Probabilités implicites dévigées (on retire la marge du bookmaker)."""
    o = np.array([odds_home, odds_draw, odds_away], dtype=float)
    if np.any(~np.isfinite(o)) or np.any(o <= 1.0):
        return None
    inv = 1.0 / o
    return inv / inv.sum()


def log_loss_multi(probs: np.ndarray, outcomes: np.ndarray) -> float:
    probs = np.clip(np.asarray(probs, dtype=float).reshape(-1, 3), 1e-12, 1.0)
    outcomes = np.asarray(outcomes, dtype=int)
    return float(-np.mean(np.log(probs[np.arange(len(outcomes)), outcomes])))


def calibration_table(probs: np.ndarray, outcomes: np.ndarray, n_bins: int = 10) -> list[dict]:
    """Courbe de calibration (toutes classes confondues) : par tranche de proba
    prédite, fréquence réelle observée."""
    probs = np.asarray(probs, dtype=float).reshape(-1, 3)
    outcomes = np.asarray(outcomes, dtype=int)
    obs = np.zeros_like(probs)
    obs[np.arange(len(outcomes)), outcomes] = 1.0
    p = probs.ravel()
    o = obs.ravel()
    bins = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(p, bins) - 1, 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        m = idx == b
        if m.sum() == 0:
            continue
        rows.append({
            "bin": f"{bins[b]:.1f}-{bins[b+1]:.1f}",
            "predit_moyen": round(float(p[m].mean()), 3),
            "observe": round(float(o[m].mean()), 3),
            "effectif": int(m.sum()),
        })
    return rows
