"""Tests du modèle de buteurs (Étape 3 / Phase 9).

La logique pure (`scorers.distribute`) est testée sans base ni modèle. Les bouts
bout-en-bout (service + API clubs) ne tournent que si les modèles sont entraînés ;
la dégradation propre en sélection, elle, est toujours vérifiée (aucun modèle requis).
"""

import os

import pytest

from pipeline import config, scorers


def _player(name, goals, xg, minutes, games, position="F"):
    return {"player_name": name, "goals": goals, "xg": xg, "npg": goals,
            "npxg": xg, "minutes": minutes, "games": games, "position": position}


# --- logique pure ----------------------------------------------------------
def test_distribute_ranks_prolific_scorer_first():
    players = [
        _player("Buteur", goals=20, xg=18.0, minutes=2700, games=30),
        _player("Milieu", goals=3, xg=2.5, minutes=2700, games=30),
        _player("Défenseur", goals=0, xg=0.5, minutes=2700, games=30),
    ]
    out = scorers.distribute(players, lam_team=1.8, top_n=5)
    assert out[0]["name"] == "Buteur"
    assert out[0]["prob"] > out[1]["prob"] > out[2]["prob"]
    for s in out:                       # probabilités valides
        assert 0.0 < s["prob"] < 1.0


def test_distribute_excludes_unavailable_player():
    players = [
        _player("Star", goals=25, xg=22.0, minutes=2700, games=30),
        _player("Doublure", goals=5, xg=4.0, minutes=900, games=20),
    ]
    out = scorers.distribute(players, lam_team=1.5, exclude={"Star"}, top_n=5)
    names = [s["name"] for s in out]
    assert "Star" not in names
    assert "Doublure" in names


def test_distribute_handles_no_usable_players():
    # minutes/jeux à zéro -> aucun joueur exploitable -> liste vide (pas de crash)
    players = [_player("Fantôme", goals=0, xg=0.0, minutes=0, games=0)]
    assert scorers.distribute(players, lam_team=1.5) == []


def test_expected_goals_sum_close_to_team_lambda():
    # la somme des buts attendus distribués ne dépasse pas lambda (normalisation)
    players = [
        _player("A", 10, 9.0, 2700, 30),
        _player("B", 6, 7.0, 2400, 28),
        _player("C", 2, 3.0, 1800, 25),
    ]
    out = scorers.distribute(players, lam_team=2.0, top_n=10)
    assert abs(sum(s["exp_goals"] for s in out) - 2.0) < 1e-6


def test_low_sample_player_is_downweighted():
    # un joueur 1 match/1 but ne doit pas battre un buteur régulier
    players = [
        _player("Régulier", goals=15, xg=14.0, minutes=2700, games=30),
        _player("EclairUnMatch", goals=1, xg=0.9, minutes=90, games=1),
    ]
    out = scorers.distribute(players, lam_team=1.8, top_n=5)
    assert out[0]["name"] == "Régulier"


# --- dégradation propre en sélection (aucun modèle requis) -----------------
def test_internationals_unavailable():
    from pipeline import service
    r = service.predict_scorers("FIFA World Cup", "France", "Brazil", neutral=True)
    assert r["available"] is False
    assert r["domain"] == config.DOMAIN_INTL
    assert "indisponibles" in r["reason"]


def test_api_scorers_international(monkeypatch):
    from fastapi.testclient import TestClient

    from pipeline import api
    c = TestClient(api.app)
    r = c.post("/api/scorers", json={"competition": "FIFA World Cup",
                                     "home": "France", "away": "Brazil", "neutral": True})
    assert r.status_code == 200
    assert r.json()["available"] is False


# --- bout-en-bout clubs (seulement si modèles entraînés) -------------------
_HAS_MODELS = os.path.exists(os.path.join(config.MODELS_DIR, "meta_club.json"))


@pytest.mark.skipif(not _HAS_MODELS, reason="modèles non entraînés")
def test_club_scorers_end_to_end():
    from pipeline import service
    service.clear_caches()
    r = service.predict_scorers("Premier League", "Man City", "Liverpool", top_n=5)
    if not r["home"]["data"]:
        pytest.skip("table players vide (base non rafraîchie)")
    assert r["available"] is True
    assert len(r["home"]["scorers"]) > 0
    # les probabilités sont décroissantes et bornées
    probs = [s["prob"] for s in r["home"]["scorers"]]
    assert probs == sorted(probs, reverse=True)
    assert all(0.0 < p < 1.0 for p in probs)
