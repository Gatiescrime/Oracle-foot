"""Configuration commune des tests.

Par défaut, on DÉSACTIVE le journal des prédictions pendant les tests : sinon, les
nombreux appels à `service.predict` écriraient dans la vraie base (`football.db`).
Les tests du journal le réactivent explicitement sur une base temporaire isolée.
"""

import pytest

from pipeline import config


@pytest.fixture(autouse=True)
def _disable_learning_side_effects(monkeypatch):
    # Journal : éviter d'écrire dans la vraie base pendant les tests.
    monkeypatch.setattr(config, "PREDICTION_LOG_ENABLED", False)
    # Correction : prédictions déterministes quel que soit un correction.json présent.
    monkeypatch.setattr(config, "CORRECTION_ENABLED", False)
