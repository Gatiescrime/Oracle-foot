"""Calibration des probabilités 1 / X / 2.

Un modèle peut être « ordonné juste » mais mal calibré : par exemple annoncer 70 %
quand l'équipe gagne en réalité 60 % du temps. La calibration corrige ça pour que,
quand on dit 60 %, l'événement arrive bien ~60 % du temps.

Méthode : régression isotonique par classe (victoire dom / nul / victoire ext),
ajustée sur des prédictions de backtest (hors échantillon d'entraînement), puis
renormalisation pour que les trois probabilités somment à 1. L'isotonique est
robuste (monotone, non paramétrique) et n'invente pas de forme arbitraire.
"""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression


class ProbabilityCalibrator:
    def __init__(self):
        self.iso = [None, None, None]
        self.fitted = False

    def fit(self, probs: np.ndarray, outcomes: np.ndarray) -> "ProbabilityCalibrator":
        """probs : (N,3) probabilités brutes [dom, nul, ext].
        outcomes : (N,) entiers 0=dom, 1=nul, 2=ext."""
        probs = np.asarray(probs, dtype=float)
        outcomes = np.asarray(outcomes, dtype=int)
        for c in range(3):
            target = (outcomes == c).astype(float)
            iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
            iso.fit(probs[:, c], target)
            self.iso[c] = iso
        self.fitted = True
        return self

    def transform(self, probs: np.ndarray) -> np.ndarray:
        probs = np.asarray(probs, dtype=float).reshape(-1, 3)
        if not self.fitted:
            return probs / probs.sum(axis=1, keepdims=True)
        cal = np.column_stack([self.iso[c].transform(probs[:, c]) for c in range(3)])
        cal = np.clip(cal, 1e-6, None)
        cal /= cal.sum(axis=1, keepdims=True)
        return cal

    def transform_one(self, p_home: float, p_draw: float, p_away: float) -> tuple[float, float, float]:
        c = self.transform(np.array([[p_home, p_draw, p_away]]))[0]
        return float(c[0]), float(c[1]), float(c[2])

    def to_dict(self) -> dict:
        if not self.fitted:
            return {"fitted": False}
        return {
            "fitted": True,
            "classes": [
                {"x": self.iso[c].X_thresholds_.tolist(),
                 "y": self.iso[c].y_thresholds_.tolist()}
                for c in range(3)
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProbabilityCalibrator":
        obj = cls()
        if not d.get("fitted"):
            return obj
        for c in range(3):
            iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
            x = np.array(d["classes"][c]["x"])
            y = np.array(d["classes"][c]["y"])
            iso.fit(x, y)
            obj.iso[c] = iso
        obj.fitted = True
        return obj
