"""Tests Phase P2 : features de marché, blend modèle/marché, garde anti-fuite."""

from __future__ import annotations

import numpy as np
import pytest

from pipeline import config, features, xgb_model
from pipeline.ensemble import blend_with_market


# --- features de marché ----------------------------------------------------
def test_market_probs_sum_to_one():
    p = features._market_probs(1.5, 4.0, 6.0)
    s = p["p_mkt_home"] + p["p_mkt_draw"] + p["p_mkt_away"]
    assert s == pytest.approx(1.0)
    assert p["p_mkt_home"] > p["p_mkt_away"]  # favori = cote la plus basse


def test_market_probs_nan_when_odds_missing():
    p = features._market_probs(None, 4.0, 6.0)
    assert np.isnan(p["p_mkt_home"])
    assert np.isnan(p["p_mkt_draw"])
    assert np.isnan(p["p_mkt_away"])


# --- blend modèle/marché ---------------------------------------------------
def test_blend_weight_zero_is_pure_model():
    model = np.array([0.5, 0.3, 0.2])
    mkt = np.array([0.4, 0.3, 0.3])
    out = blend_with_market(model, mkt, 0.0)
    assert out == pytest.approx(model)


def test_blend_weight_one_is_pure_market():
    model = np.array([0.5, 0.3, 0.2])
    mkt = np.array([0.4, 0.3, 0.3])
    out = blend_with_market(model, mkt, 1.0)
    assert out == pytest.approx(mkt)


def test_blend_intermediate_sums_to_one():
    out = blend_with_market(np.array([0.6, 0.25, 0.15]),
                            np.array([0.4, 0.35, 0.25]), 0.5)
    assert out.sum() == pytest.approx(1.0)
    assert out[0] == pytest.approx(0.5)  # (0.6+0.4)/2


def test_blend_none_market_returns_model():
    model = np.array([0.5, 0.3, 0.2])
    assert blend_with_market(model, None, 0.7) is model


def test_blend_clips_weight():
    model = np.array([0.5, 0.3, 0.2])
    mkt = np.array([0.4, 0.3, 0.3])
    # poids hors bornes -> ramené dans [0,1] (ici 1.0 -> marché pur)
    assert blend_with_market(model, mkt, 2.0) == pytest.approx(mkt)


# --- liste de features selon la config -------------------------------------
def test_active_feature_cols_toggle(monkeypatch):
    # On isole le levier marché (P2) : l'enrichissement P5 et le contexte P6 neutralisés.
    monkeypatch.setattr(config, "P5_FEATURES_ENABLED", False)
    monkeypatch.setattr(config, "P6_FEATURES_ENABLED", False)
    monkeypatch.setattr(config, "MARKET_FEATURES_ENABLED", False)
    base = xgb_model.active_feature_cols()
    assert "p_mkt_home" not in base
    monkeypatch.setattr(config, "MARKET_FEATURES_ENABLED", True)
    withmkt = xgb_model.active_feature_cols()
    assert withmkt[-3:] == xgb_model.MARKET_FEATURE_COLS
    assert len(withmkt) == len(base) + 3
