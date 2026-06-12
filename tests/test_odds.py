"""Tests Phase P4 : agrégation de cotes multi-bookmakers (line shopping).

Tout le parsing est testé HORS LIGNE sur un échantillon fidèle à la forme
documentée de the-odds-api (aucun appel réseau). L'endpoint live n'est testé que
si une clé est configurée ET les modèles entraînés.
"""

from __future__ import annotations

import json
import os

import pytest

from pipeline import config, odds_api

# Échantillon réduit, fidèle à la réponse de the-odds-api (h2h + totals).
SAMPLE = [{
    "id": "evt1",
    "home_team": "Canada",
    "away_team": "Bosnia & Herzegovina",
    "commence_time": "2026-06-12T19:00:00Z",
    "bookmakers": [
        {"key": "unibet", "title": "Unibet", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Canada", "price": 1.85},
                {"name": "Bosnia & Herzegovina", "price": 4.9},
                {"name": "Draw", "price": 3.55}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": 2.20, "point": 2.5},
                {"name": "Under", "price": 1.64, "point": 2.5}]}]},
        {"key": "pinnacle", "title": "Pinnacle", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Canada", "price": 1.92},        # meilleur prix domicile
                {"name": "Bosnia & Herzegovina", "price": 5.10},  # meilleur prix extérieur
                {"name": "Draw", "price": 3.40}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": 2.05, "point": 3.5},   # mauvais palier -> ignoré
                {"name": "Under", "price": 1.80, "point": 2.5}]}]},  # meilleur under
    ],
}]


# --- parsing ---------------------------------------------------------------
def test_parse_events_robust():
    assert odds_api.parse_events(json.dumps(SAMPLE).encode()) == SAMPLE
    assert odds_api.parse_events(b"not json") == []
    assert odds_api.parse_events(b'{"not": "a list"}') == []


def test_best_prices_picks_highest_per_outcome():
    best = odds_api.best_prices(SAMPLE[0])
    assert best["n_books"] == 2
    # 1X2 : meilleur prix = cote la plus haute parmi les books
    assert best["h2h"]["home"]["odds"] == pytest.approx(1.92)
    assert best["h2h"]["home"]["book"] == "Pinnacle"
    assert best["h2h"]["away"]["odds"] == pytest.approx(5.10)
    assert best["h2h"]["draw"]["odds"] == pytest.approx(3.55)


def test_best_prices_totals_only_2_5():
    best = odds_api.best_prices(SAMPLE[0])
    # Over : le 3.5 de Pinnacle est ignoré -> seul l'Over 2.5 d'Unibet compte
    assert best["totals"]["over"]["odds"] == pytest.approx(2.20)
    assert best["totals"]["over"].get("point") == 2.5
    # Under : meilleur = 1.80 (Pinnacle) vs 1.64 (Unibet)
    assert best["totals"]["under"]["odds"] == pytest.approx(1.80)


# --- appariement de match --------------------------------------------------
def test_find_event_fuzzy_and_overrides():
    # "Bosnia and Herzegovina" (notre nom) vs "Bosnia & Herzegovina" (API)
    ev = odds_api.find_event(SAMPLE, "Canada", "Bosnia and Herzegovina")
    assert ev is not None and ev["id"] == "evt1" and ev["_swapped"] is False


def test_find_event_handles_swapped_order():
    ev = odds_api.find_event(SAMPLE, "Bosnia and Herzegovina", "Canada")
    assert ev is not None and ev["_swapped"] is True


def test_find_event_returns_none_when_no_match():
    assert odds_api.find_event(SAMPLE, "Real Madrid", "Barcelona") is None
    assert odds_api.find_event([], "Canada", "Bosnia") is None


def test_team_override_usa():
    assert odds_api.TEAM_OVERRIDES.get("usa") == "United States"


# --- configuration / repli -------------------------------------------------
def test_sport_key_mapping():
    assert odds_api.sport_key_for("Premier League") == "soccer_epl"
    assert odds_api.sport_key_for("FIFA World Cup") == "soccer_fifa_world_cup"
    assert odds_api.sport_key_for("Compétition inconnue") is None


def test_fetch_without_key_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "ODDS_API_KEY", "")
    assert odds_api.configured() is False
    assert odds_api.fetch_odds("soccer_epl") == []


# --- service : repli football-data sans clé --------------------------------
@pytest.mark.skipif(not os.path.exists(os.path.join(config.MODELS_DIR, "meta_club.json")),
                    reason="modèles non entraînés")
def test_live_odds_fallback_without_key(monkeypatch):
    from pipeline import odds_api as oa
    from pipeline import service
    monkeypatch.setattr(config, "ODDS_API_KEY", "")
    monkeypatch.setattr(oa.config, "ODDS_API_KEY", "")

    comp = config.CLUB_LEAGUES[0]["competition"]
    teams = service.list_teams("club")
    home, away = teams[0]["name"], teams[1]["name"]
    r = service.live_odds(comp, home, away)
    assert r["configured"] is False
    # repli : soit des cotes football-data historiques, soit indisponible proprement
    assert r["source"] in ("football-data", "none")
    assert "reason" in r


@pytest.mark.skipif(
    not config.ODDS_API_KEY or not os.path.exists(os.path.join(config.MODELS_DIR, "meta_intl.json")),
    reason="clé odds-api absente ou modèles non entraînés")
def test_odds_status_configured():
    from pipeline import service
    st = service.odds_status()
    assert st["configured"] is True
    assert "FIFA World Cup" in st["covered_competitions"]
