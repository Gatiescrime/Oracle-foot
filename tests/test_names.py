"""Tests hors-ligne de la correspondance des noms (aucun réseau)."""

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
