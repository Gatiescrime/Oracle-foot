"""Tests bout-en-bout du service et de l'API (sautés si modèles non entraînés)."""

import os

import pytest

from pipeline import config

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(config.MODELS_DIR, "meta_club.json")),
    reason="modèles non entraînés (lancer python -m pipeline.train)")


def test_predict_contract_complete():
    from pipeline import service
    p = service.predict("Premier League", "Man City", "Liverpool", neutral=False)
    for key in ["p_home_win", "p_draw", "p_away_win", "exp_home_goals",
                "exp_away_goals", "most_likely_score", "p_over_2_5", "p_btts",
                "score_matrix", "home", "away", "competition", "domain"]:
        assert key in p
    s = p["p_home_win"] + p["p_draw"] + p["p_away_win"]
    assert abs(s - 1.0) < 1e-2


def test_neutral_lowers_home_prob_via_service():
    from pipeline import service
    home = service.predict("FIFA World Cup", "France", "Brazil", neutral=False)["p_home_win"]
    neut = service.predict("FIFA World Cup", "France", "Brazil", neutral=True)["p_home_win"]
    assert home > neut


def test_unknown_team_raises():
    from pipeline import service
    with pytest.raises(ValueError):
        service.predict("Premier League", "Equipe Inexistante XYZ", "Liverpool")


def test_api_endpoints():
    from fastapi.testclient import TestClient

    from pipeline.api import app
    c = TestClient(app)
    assert c.get("/api/competitions").status_code == 200
    assert len(c.get("/api/teams?domain=club").json()["teams"]) > 50
    fx = c.get("/api/fixtures").json()["fixtures"]
    assert len(fx) > 0
    r = c.post("/api/predict", json={
        "competition": "FIFA World Cup",
        "home": fx[0]["home"], "away": fx[0]["away"], "neutral": True})
    assert r.status_code == 200
    assert abs(sum(r.json()[k] for k in ["p_home_win", "p_draw", "p_away_win"]) - 1) < 1e-2


def test_predict_without_qualitative_is_statistical_only():
    """Couche actu OFF (défaut) : aucun ajustement, prédiction = socle statistique."""
    from pipeline import service
    p = service.predict("Premier League", "Man City", "Liverpool",
                        use_qualitative=False)
    assert p["qualitative_enabled"] is False
    assert "qualitative" not in p


def test_predict_with_qualitative_toggle(monkeypatch):
    """use_qualitative=True fait passer un ajustement borné, sans appel réseau réel."""
    from pipeline import service

    class _FakeLayer:
        enabled = False
        def adjust(self, home, away, competition, ref_date=None, enabled_override=None):
            if not enabled_override:
                return None
            return {"mult_dom": 1.1, "mult_ext": 0.9, "facteurs": ["test"],
                    "faits": [], "confiance": 0.5, "source": "claude+web"}
        def calls_today(self):
            return 7

    monkeypatch.setattr(service, "_qualitative", _FakeLayer())
    on = service.predict("Premier League", "Man City", "Liverpool", use_qualitative=True)
    assert on["qualitative_enabled"] is True
    assert on["qualitative"]["mult_dom"] == 1.1
    off = service.predict("Premier League", "Man City", "Liverpool", use_qualitative=False)
    assert off["qualitative_enabled"] is False and "qualitative" not in off


def test_qualitative_status_endpoint():
    from fastapi.testclient import TestClient

    from pipeline.api import app
    c = TestClient(app)
    s = c.get("/api/qualitative/status").json()
    for key in ["enabled_default", "calls_today", "cache_ttl_hours",
                "max_web_uses", "news_window_days"]:
        assert key in s
    assert isinstance(s["calls_today"], int)
