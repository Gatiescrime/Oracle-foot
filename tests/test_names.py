"""Tests hors-ligne de la correspondance des noms (aucun réseau)."""

import pytest

from pipeline import names


def test_slugify_strips_accents_and_punct():
    assert names.slugify("Saint-Étienne") == "saint_etienne"
    assert names.slugify("Nott'm Forest") == "nott_m_forest"
    assert names.slugify("Bayern München") == "bayern_munchen"


def test_match_epl_basic():
    und = ["Manchester City", "Manchester United", "Newcastle United",
           "Wolverhampton Wanderers", "Arsenal"]
    fd = ["Man City", "Man United", "Newcastle", "Wolves", "Arsenal"]
    m = names.match_sets(und, fd)
    assert m["Manchester City"] == "Man City"
    assert m["Manchester United"] == "Man United"
    assert m["Newcastle United"] == "Newcastle"
    assert m["Wolverhampton Wanderers"] == "Wolves"
    assert m["Arsenal"] == "Arsenal"


def test_match_laliga_tricky_pairs():
    """Les paires piégeuses (Atletico/Athletic, Real Sociedad/Real Madrid)
    doivent être appariées sans collision grâce aux overrides + appariement optimal."""
    und = ["Atletico Madrid", "Athletic Club", "Real Sociedad", "Real Madrid",
           "Real Betis", "Espanyol", "Rayo Vallecano", "Celta Vigo"]
    fd = ["Ath Madrid", "Ath Bilbao", "Sociedad", "Real Madrid",
          "Betis", "Espanol", "Vallecano", "Celta"]
    m = names.match_sets(und, fd)
    assert m["Atletico Madrid"] == "Ath Madrid"
    assert m["Athletic Club"] == "Ath Bilbao"
    assert m["Real Sociedad"] == "Sociedad"
    assert m["Real Madrid"] == "Real Madrid"
    assert m["Real Betis"] == "Betis"
    assert m["Espanyol"] == "Espanol"
    assert m["Rayo Vallecano"] == "Vallecano"
    assert m["Celta Vigo"] == "Celta"
    # bijection : pas deux libellés understat sur le même nom football-data
    assert len(set(m.values())) == len(m)


@pytest.mark.parametrize("api_label, canonical", [
    ("USA", "United States"),
    ("United States of America", "United States"),
    ("Korea Republic", "South Korea"),
    ("Czechia", "Czech Republic"),
    ("Türkiye", "Turkey"),
    ("Côte d'Ivoire", "Ivory Coast"),
    ("UAE", "United Arab Emirates"),
    ("Macedonia", "North Macedonia"),
    ("FYR Macedonia", "North Macedonia"),
    ("Bosnia", "Bosnia and Herzegovina"),
    ("Trinidad & Tobago", "Trinidad and Tobago"),
    ("Hong Kong, China", "Hong Kong"),
    ("Curaçao", "Curaçao"),                 # accents gérés par slugify, même clé
    ("Cabo Verde", "Cape Verde"),
])
def test_alias_key_unifies_variants(api_label, canonical):
    """Un libellé the-odds-api/bookmaker et notre nom canonique produisent la MÊME
    clé d'appariement (accents/casse via slugify + alias de mots différents)."""
    assert names.alias_key(api_label) == names.alias_key(canonical)


def test_northern_ireland_not_confused_with_ireland():
    """Garde-fou : « Northern Ireland » ne doit JAMAIS tomber sur la République
    d'Irlande (deux sélections distinctes)."""
    assert names.alias_key("Ireland") == names.alias_key("Republic of Ireland")
    assert names.alias_key("Northern Ireland") != names.alias_key("Republic of Ireland")


def test_find_event_matches_with_alias_and_swap():
    """find_event apparie malgré un libellé différent (USA↔United States) et un
    ordre domicile/extérieur inversé chez le bookmaker."""
    from pipeline import odds_api
    events = [
        {"home_team": "Paraguay", "away_team": "USA", "commence_time": "x"},
        {"home_team": "Mexico", "away_team": "Canada", "commence_time": "y"},
    ]
    ev = odds_api.find_event(events, "United States", "Paraguay")
    assert ev is not None
    assert ev["away_team"] == "USA" and ev["_swapped"] is True
