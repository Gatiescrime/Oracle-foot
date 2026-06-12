"""Tests du moteur de paris (Phase P1) : devig, value, Kelly, CLV, anti-fuite."""

from __future__ import annotations

import pytest

from pipeline import devig, betting


# --- devig -----------------------------------------------------------------
def test_devig_proportional_sums_to_one():
    p = devig.proportional([2.0, 3.5, 4.0])
    assert p is not None
    assert abs(sum(p) - 1.0) < 1e-9


def test_devig_power_sums_to_one():
    p = devig.power([2.0, 3.5, 4.0])
    assert p is not None
    assert abs(sum(p) - 1.0) < 1e-9


def test_devig_fair_market_passthrough():
    # marché déjà équitable (somme 1/o = 1) -> probabilités quasi inchangées
    odds = [2.0, 2.0]  # 0.5 + 0.5 = 1.0, marge nulle
    p = devig.proportional(odds)
    assert p == pytest.approx([0.5, 0.5])
    assert devig.overround(odds) == pytest.approx(0.0, abs=1e-9)


def test_devig_overround_positive():
    # cotes serrées -> marge nettement positive
    assert devig.overround([1.5, 1.5]) > 0.3


def test_devig_invalid_returns_none():
    assert devig.proportional([2.0, 1.0]) is None  # cote 1.0 invalide
    assert devig.power([0.0, 3.0]) is None


def test_devig_power_differs_and_normalises():
    # sur un marché à marge positive, les deux méthodes somment à 1 mais diffèrent
    odds = [1.3, 4.5, 9.0]
    assert devig.overround(odds) > 0
    prop = devig.proportional(odds)
    pw = devig.power(odds)
    assert prop is not None and pw is not None
    assert abs(sum(pw) - 1.0) < 1e-9
    assert pw != pytest.approx(prop)  # méthodes distinctes


# --- Kelly -----------------------------------------------------------------
def test_kelly_zero_when_no_edge():
    # proba = proba implicite -> pas d'edge -> Kelly nul
    f = betting._kelly_fraction(0.5, 2.0)
    assert f == pytest.approx(0.0, abs=1e-9)


def test_kelly_positive_with_edge():
    # 60 % de chances à cote 2.0 -> Kelly = (1*0.6 - 0.4)/1 = 0.2
    f = betting._kelly_fraction(0.6, 2.0)
    assert f == pytest.approx(0.2)


def test_kelly_negative_when_value_against():
    assert betting._kelly_fraction(0.4, 2.0) < 0


# --- détection de value ----------------------------------------------------
def _sel(prob, dec, close=None, won=False, market="1X2", sel="home", league="L"):
    return {"date": "2024-01-01", "league": league, "domain": "club",
            "market": market, "selection": sel, "model_prob": prob,
            "dec_odds": dec, "max_odds": dec, "close_odds": close, "won": won}


def test_find_bets_edge_threshold():
    sels = [
        _sel(0.60, 2.0),   # edge = 0.20 -> parié
        _sel(0.50, 2.0),   # edge = 0.00 -> ignoré
        _sel(0.52, 2.0),   # edge = 0.04 < 0.05 -> ignoré
    ]
    bets = betting.find_bets(sels, edge=0.05)
    assert len(bets) == 1
    assert bets[0]["edge"] == pytest.approx(0.20)


def test_find_bets_uses_open_price_not_close():
    # cote d'ouverture 2.0 (edge faible) vs clôture 5.0 : la clôture ne doit JAMAIS
    # servir à décider -> aucun pari sur cette sélection.
    s = _sel(0.50, dec=2.0, close=5.0)
    bets = betting.find_bets([s], edge=0.05)
    assert bets == []


# --- simulation flat vs Kelly ----------------------------------------------
def test_simulate_flat_profit_on_win():
    bets = betting.find_bets([_sel(0.60, 2.0, won=True)], edge=0.05)
    res = betting.simulate(bets, mode="flat", flat_stake=1.0,
                           commission=0.0, bankroll0=100.0)
    assert res["n_bets"] == 1
    assert res["profit"] == pytest.approx(1.0)        # gain net = (2-1)*1
    assert res["hit_rate"] == 1.0


def test_simulate_flat_loss():
    bets = betting.find_bets([_sel(0.60, 2.0, won=False)], edge=0.05)
    res = betting.simulate(bets, mode="flat", flat_stake=1.0, bankroll0=100.0)
    assert res["profit"] == pytest.approx(-1.0)
    assert res["hit_rate"] == 0.0


def test_simulate_commission_reduces_win():
    bets = betting.find_bets([_sel(0.60, 2.0, won=True)], edge=0.05)
    res = betting.simulate(bets, mode="flat", flat_stake=1.0,
                           commission=0.05, bankroll0=100.0)
    assert res["profit"] == pytest.approx(0.95)       # (2-1)*1*(1-0.05)


def test_simulate_kelly_stake_scales_with_bankroll():
    bets = betting.find_bets([_sel(0.60, 2.0, won=True)], edge=0.05)
    res = betting.simulate(bets, mode="kelly", kelly_fraction=0.5,
                           bankroll0=100.0, commission=0.0, kelly_cap=1e9)
    # mise = 100 * 0.5 * 0.2 = 10 ; gain = (2-1)*10 = 10
    assert res["staked"] == pytest.approx(10.0)
    assert res["profit"] == pytest.approx(10.0)


# --- CLV --------------------------------------------------------------------
def test_clv_positive_when_entry_better_than_close():
    bets = betting.find_bets([_sel(0.60, dec=2.0, close=1.8, won=True)], edge=0.05)
    stats = betting.clv_stats(bets)
    # CLV = 2.0/1.8 - 1 > 0 -> on a battu la clôture (valeur arrondie à 4 décimales)
    assert stats["clv_mean"] == pytest.approx(2.0 / 1.8 - 1.0, abs=1e-3)
    assert stats["clv_beat_rate"] == 1.0


def test_clv_negative_when_entry_worse_than_close():
    bets = betting.find_bets([_sel(0.60, dec=2.0, close=2.5, won=False)], edge=0.05)
    stats = betting.clv_stats(bets)
    assert stats["clv_mean"] < 0
    assert stats["clv_beat_rate"] == 0.0


def test_clv_none_without_closing_odds():
    bets = betting.find_bets([_sel(0.60, dec=2.0, close=None)], edge=0.05)
    stats = betting.clv_stats(bets)
    assert stats["clv_mean"] is None
    assert stats["n_clv"] == 0
