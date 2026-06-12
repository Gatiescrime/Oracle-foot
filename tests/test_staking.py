"""Tests Phase P3 : recommandation de mise (Kelly fractionné, bornes, garde-fous)."""

from __future__ import annotations

import os

import pytest

from pipeline import config, staking


# --- Kelly -----------------------------------------------------------------
def test_kelly_zero_without_edge():
    assert staking.kelly_fraction(0.5, 2.0) == pytest.approx(0.0)


def test_kelly_positive_with_edge():
    assert staking.kelly_fraction(0.6, 2.0) == pytest.approx(0.2)


def test_kelly_clamped_non_negative():
    assert staking.kelly_fraction(0.3, 2.0) == 0.0  # value contre -> jamais < 0


# --- recommandation : value vs pas de value --------------------------------
def test_recommend_no_value_advises_against_betting():
    rec = staking.recommend(0.50, 2.0, 100.0)   # edge nul
    assert rec["value"] is False
    assert rec["stake_amount"] == 0.0
    assert "déconseillé" in rec["message"].lower()


def test_recommend_value_positive_stake():
    rec = staking.recommend(0.60, 2.0, 100.0, edge_threshold=0.05,
                            kelly_frac=0.25, max_stake_frac=0.05)
    assert rec["value"] is True
    assert rec["edge"] == pytest.approx(0.20)
    # Kelly plein = 0.2 ; quart de Kelly = 0.05 ; sous le plafond 0.05 -> mise = 5
    assert rec["stake_fraction"] == pytest.approx(0.05)
    assert rec["stake_amount"] == pytest.approx(5.0)


def test_recommend_stake_capped():
    # gros edge -> Kelly voudrait beaucoup, mais on plafonne à 2 % du capital
    rec = staking.recommend(0.90, 3.0, 1000.0, edge_threshold=0.05,
                            kelly_frac=0.5, max_stake_frac=0.02)
    assert rec["stake_fraction"] == pytest.approx(0.02)
    assert rec["stake_amount"] == pytest.approx(20.0)
    assert rec["capped_at_fraction"] == pytest.approx(0.02)


def test_recommend_invalid_inputs():
    assert staking.recommend(0.6, 1.0, 100.0)["valid"] is False   # cote <= 1
    assert staking.recommend(0.6, 2.0, 0.0)["valid"] is False     # capital nul
    assert staking.recommend(1.5, 2.0, 100.0)["valid"] is False   # proba absurde


def test_recommend_always_has_warnings_and_no_guarantee():
    rec = staking.recommend(0.60, 2.0, 100.0)
    assert len(rec["warnings"]) >= 3
    joined = " ".join(rec["warnings"]).lower()
    assert "aucune garantie" in joined
    assert "perdre" in joined


def test_recommend_commission_reduces_ev():
    no_comm = staking.recommend(0.60, 2.0, 100.0, commission=0.0)["ev_per_unit"]
    with_comm = staking.recommend(0.60, 2.0, 100.0, commission=0.10)["ev_per_unit"]
    assert with_comm < no_comm


# --- endpoint (si modèles entraînés) ---------------------------------------
@pytest.mark.skipif(not os.path.exists(os.path.join(config.MODELS_DIR, "meta_club.json")),
                    reason="modèles non entraînés")
def test_stake_endpoint_end_to_end():
    from fastapi.testclient import TestClient
    from pipeline.api import app

    client = TestClient(app)
    comp = config.CLUB_LEAGUES[0]["competition"]
    teams = client.get("/api/teams", params={"domain": "club"}).json()["teams"]
    assert len(teams) >= 2
    home, away = teams[0]["name"], teams[1]["name"]

    # On choisit l'issue la plus probable selon le modèle, puis une cote 30 % au-dessus
    # du « juste prix » 1/p : on garantit ainsi de la value, quel que soit le modèle
    # entraîné (le test reste robuste aux ré-entraînements).
    pred = client.post("/api/predict", json={
        "competition": comp, "home": home, "away": away}).json()
    probs = {"home": pred["p_home_win"], "draw": pred["p_draw"], "away": pred["p_away_win"]}
    sel = max(probs, key=probs.get)
    odds = round(1.3 / probs[sel], 2)  # edge ≈ +30 %

    r = client.post("/api/stake", json={
        "competition": comp, "home": home, "away": away,
        "selection": sel, "odds": odds, "bankroll": 100.0})
    assert r.status_code == 200
    body = r.json()
    assert "value" in body and "stake_amount" in body and body["valid"] is True
    assert 0.0 <= body["stake_amount"] <= 5.0  # plafond 5 % de 100
    assert len(body["warnings"]) >= 3
