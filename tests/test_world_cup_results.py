"""PHASE A — Basculement des résultats de la Coupe du Monde (« à venir » → « joués »).

Quand un match de Coupe du Monde se termine, son score apparaît dans le CSV
martj42. Au prochain refresh, ce match doit :
  - quitter le calendrier « à venir » (table `fixtures`) ;
  - rejoindre l'historique des matchs joués (table `matches`) — donc alimenter
    l'Elo et les features, et influencer les prédictions suivantes ;
  - disparaître de la liste « à venir » servie au site.

On reproduit DEUX refresh successifs (même affiche, sans score puis avec score),
sans JAMAIS toucher au réseau (CSV martj42 simulé) ni réentraîner.
"""

import io

import pandas as pd
import pytest

from pipeline import config, db, refresh, service
from pipeline.sources import martj42

HEADER = "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral"


def _csv(rows: list[str]) -> bytes:
    return ("\n".join([HEADER] + rows) + "\n").encode("utf-8")


def _future() -> str:
    return (pd.Timestamp.today().normalize() + pd.Timedelta(days=20)).strftime("%Y-%m-%d")


def _past() -> str:
    return (pd.Timestamp.today().normalize() - pd.Timedelta(days=3)).strftime("%Y-%m-%d")


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Isole la base dans un fichier temporaire (config.DB_PATH lu à l'appel)."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "wc.db"))
    service.clear_caches()
    # Pas de clé odds en test : la liste « à venir » retombe sur la table fixtures.
    monkeypatch.setattr(service.odds_api, "configured", lambda: False)
    yield
    service.clear_caches()


def _ingest(csv_bytes, monkeypatch):
    """Un cycle de refresh sélections : parse martj42 + reconstruit la base."""
    monkeypatch.setattr(martj42.http, "fetch", lambda *a, **k: csv_bytes)
    results, fixtures = martj42.fetch_all(use_cache=False)
    conn = db.connect()
    db.reset_schema(conn)
    refresh.ingest_internationals(conn, results) if not results.empty else None
    refresh.ingest_fixtures(conn, fixtures) if not fixtures.empty else None
    conn.close()
    service.clear_caches()
    return results, fixtures


def test_martj42_routes_scored_vs_unscored():
    """Le parsing martj42 sépare bien joué (avec score) et à venir (sans score)."""
    csv = _csv([
        f"{_past()},United States,Paraguay,4,1,FIFA World Cup,,United States,TRUE",
        f"{_future()},Brazil,Argentina,,,FIFA World Cup,,United States,TRUE",
    ])
    import unittest.mock as m
    with m.patch.object(martj42.http, "fetch", lambda *a, **k: csv):
        results, fixtures = martj42.fetch_all(use_cache=False)
    assert ((results["home_team"] == "United States") & (results["away_team"] == "Paraguay")).any()
    assert results.loc[0, "home_goals"] == 4 and results.loc[0, "away_goals"] == 1
    # le match joué n'est PAS dans le calendrier ; le match à venir, si
    assert not ((fixtures["home_team"] == "United States")).any()
    assert ((fixtures["home_team"] == "Brazil") & (fixtures["away_team"] == "Argentina")).any()


def test_finished_world_cup_match_moves_from_upcoming_to_history(temp_db, monkeypatch):
    future, past = _future(), _past()

    # --- Refresh 1 : USA-Paraguay est À VENIR (score vide) -------------------
    _ingest(_csv([
        f"{future},United States,Paraguay,,,FIFA World Cup,,United States,TRUE",
    ]), monkeypatch)

    conn = db.connect()
    n_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    n_fixtures = conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0]
    conn.close()
    assert n_matches == 0 and n_fixtures == 1            # à venir, pas encore joué

    up = service.upcoming_matches(days=60)
    assert any(m["home"] == "United States" and m["away"] == "Paraguay"
               for m in up["matches"])                    # visible dans « à venir »

    # --- Refresh 2 : le match est TERMINÉ (USA 4-1, date passée) -------------
    _ingest(_csv([
        f"{past},United States,Paraguay,4,1,FIFA World Cup,,United States,TRUE",
    ]), monkeypatch)

    conn = db.connect()
    row = conn.execute(
        "SELECT home_goals, away_goals, neutral FROM matches "
        "WHERE competition='FIFA World Cup'").fetchone()
    n_fixtures = conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0]
    conn.close()

    assert row is not None and (row["home_goals"], row["away_goals"]) == (4, 1)  # alimente Elo/features
    assert n_fixtures == 0                                # quitté le calendrier

    up = service.upcoming_matches(days=60)
    assert not any(m["home"] == "United States" and m["away"] == "Paraguay"
                   for m in up["matches"])                # disparu de « à venir »
