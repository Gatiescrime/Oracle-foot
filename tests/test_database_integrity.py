"""Tests d'intégration sur la base réelle `data/football.db`.

Skippés si la base n'existe pas (lancer `python -m pipeline.refresh` d'abord).
Vérifient le critère d'acceptation de la Phase 1 : nombre de matchs et absence
de doublons, plus intégrité référentielle et couverture xG.
"""

import os

import pytest

from pipeline import config, db

pytestmark = pytest.mark.skipif(
    not os.path.exists(config.DB_PATH),
    reason="base absente — lancer `python -m pipeline.refresh`",
)


@pytest.fixture(scope="module")
def conn():
    c = db.connect()
    yield c
    c.close()


def test_match_counts_above_thresholds(conn):
    clubs = conn.execute("SELECT COUNT(*) FROM matches WHERE domain='club'").fetchone()[0]
    intl = conn.execute("SELECT COUNT(*) FROM matches WHERE domain='international'").fetchone()[0]
    # seuils planchers (robustes à l'ajout de nouveaux matchs au fil des saisons)
    assert clubs >= 8000, f"trop peu de matchs clubs: {clubs}"
    assert intl >= 45000, f"trop peu de matchs sélections: {intl}"


def test_no_duplicate_match_id(conn):
    dups = conn.execute(
        "SELECT match_id, COUNT(*) c FROM matches GROUP BY match_id HAVING c > 1"
    ).fetchall()
    assert dups == []


def test_no_duplicate_fixture_key(conn):
    dups = conn.execute(
        """SELECT domain, date, home_team_id, away_team_id, COUNT(*) c
           FROM matches GROUP BY domain, date, home_team_id, away_team_id HAVING c > 1"""
    ).fetchall()
    assert dups == []


def test_referential_integrity(conn):
    orphans = conn.execute(
        """SELECT COUNT(*) FROM matches m
           WHERE m.home_team_id NOT IN (SELECT team_id FROM teams)
              OR m.away_team_id NOT IN (SELECT team_id FROM teams)"""
    ).fetchone()[0]
    assert orphans == 0


def test_no_self_matches(conn):
    self_games = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE home_team_id = away_team_id"
    ).fetchone()[0]
    assert self_games == 0


def test_xg_coverage_recent_clubs(conn):
    total, with_xg = conn.execute(
        """SELECT COUNT(*), SUM(CASE WHEN home_xg IS NOT NULL THEN 1 ELSE 0 END)
           FROM matches WHERE domain='club'"""
    ).fetchone()
    assert with_xg / total > 0.5, f"couverture xG insuffisante: {with_xg}/{total}"


def test_odds_coverage_clubs(conn):
    total, with_odds = conn.execute(
        """SELECT COUNT(*), SUM(CASE WHEN odds_home IS NOT NULL THEN 1 ELSE 0 END)
           FROM matches WHERE domain='club'"""
    ).fetchone()
    assert with_odds / total > 0.8, f"couverture cotes insuffisante: {with_odds}/{total}"


def test_fixtures_present(conn):
    n = conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0]
    assert n > 0


def test_worldcup_2026_in_fixtures(conn):
    wc = conn.execute(
        "SELECT COUNT(*) FROM fixtures WHERE competition LIKE '%World Cup%'"
    ).fetchone()[0]
    assert wc > 0, "le calendrier CdM 2026 devrait être présent dans les fixtures"
