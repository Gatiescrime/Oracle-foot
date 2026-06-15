"""PHASE 1 — journal des prédictions (apprentissage par l'expérience).

Aucun réseau, aucun modèle : base isolée en mémoire de fichier temporaire. On vérifie
l'enregistrement, la déduplication, le RÈGLEMENT avec métriques, et surtout
l'ANTI-FUITE : jamais de résultat connu au moment de journaliser.
"""

import sqlite3

import pytest

from pipeline import config, db, journal


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Base temporaire avec le schéma complet (SCHEMA + tables persistantes)."""
    path = str(tmp_path / "j.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "PREDICTION_LOG_ENABLED", True)   # réactivé ici
    monkeypatch.setattr(journal, "model_version", lambda domain: "test-v1")
    conn = db.connect()
    db.reset_schema(conn)                 # crée matches/fixtures/teams…
    conn.close()
    yield path


def _team(conn, tid, name):
    db.upsert_team(conn, tid, name, config.DOMAIN_INTL, None)


def _fixture(conn, hid, aid, date, comp="FIFA World Cup"):
    conn.execute(
        "INSERT OR REPLACE INTO fixtures(fixture_id,date,competition,domain,"
        "home_team_id,away_team_id,neutral) VALUES (?,?,?,?,?,?,1)",
        (f"{hid}-{aid}-{date}", date, comp, config.DOMAIN_INTL, hid, aid))


def _played(conn, hid, aid, date, hg, ag, comp="FIFA World Cup"):
    conn.execute(
        "INSERT OR REPLACE INTO matches(match_id,date,competition,domain,"
        "home_team_id,away_team_id,home_goals,away_goals,neutral) "
        "VALUES (?,?,?,?,?,?,?,?,1)",
        (f"m-{hid}-{aid}-{date}", date, comp, config.DOMAIN_INTL, hid, aid, hg, ag))


PRED = {"p_home_win": 0.55, "p_draw": 0.25, "p_away_win": 0.20,
        "exp_home_goals": 1.8, "exp_away_goals": 1.1, "most_likely_score": [1, 1]}


def test_logs_only_real_upcoming_fixture(temp_db):
    conn = db.connect()
    _team(conn, "n_a", "A"); _team(conn, "n_b", "B")
    _fixture(conn, "n_a", "n_b", "2026-06-20")
    conn.commit(); conn.close()

    # match au calendrier -> journalisé ; match hors calendrier -> ignoré
    assert journal.maybe_log(PRED, config.DOMAIN_INTL, "FIFA World Cup", "n_a", "n_b", True, False) is True
    assert journal.maybe_log(PRED, config.DOMAIN_INTL, "FIFA World Cup", "n_a", "n_x", True, False) is False

    conn = db.connect()
    rows = conn.execute("SELECT * FROM predictions_log").fetchall()
    conn.close()
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "pending"
    assert r["match_date"] == "2026-06-20"
    # ANTI-FUITE : aucune colonne de résultat au moment de journaliser
    assert r["actual_home_goals"] is None and r["outcome"] is None and r["rps"] is None


def test_anti_leak_refuses_when_result_already_known(temp_db):
    """Si le résultat est DÉJÀ connu (match joué), on ne journalise pas une
    « prédiction » : impossible de prédire après coup."""
    conn = db.connect()
    _team(conn, "n_a", "A"); _team(conn, "n_b", "B")
    _fixture(conn, "n_a", "n_b", "2026-06-20")
    _played(conn, "n_a", "n_b", "2026-06-20", 3, 0)     # déjà joué
    conn.commit(); conn.close()

    assert journal.maybe_log(PRED, config.DOMAIN_INTL, "FIFA World Cup", "n_a", "n_b", True, False) is False
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) FROM predictions_log").fetchone()[0]
    conn.close()
    assert n == 0


def test_dedupe_same_match_same_version(temp_db):
    conn = db.connect()
    _team(conn, "n_a", "A"); _team(conn, "n_b", "B")
    _fixture(conn, "n_a", "n_b", "2026-06-20")
    conn.commit(); conn.close()
    for _ in range(3):
        journal.maybe_log(PRED, config.DOMAIN_INTL, "FIFA World Cup", "n_a", "n_b", True, False)
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) FROM predictions_log").fetchone()[0]
    conn.close()
    assert n == 1                       # une seule ligne malgré 3 appels


def test_settlement_fills_result_and_metrics(temp_db):
    conn = db.connect()
    _team(conn, "n_a", "A"); _team(conn, "n_b", "B")
    _fixture(conn, "n_a", "n_b", "2026-06-20")
    conn.commit(); conn.close()
    journal.maybe_log(PRED, config.DOMAIN_INTL, "FIFA World Cup", "n_a", "n_b", True, False)

    # règlement AVANT que le résultat n'existe : rien à régler
    assert journal.settle_pending() == 0

    # le match est joué (domicile gagne 2-0) -> on règle
    conn = db.connect(); _played(conn, "n_a", "n_b", "2026-06-20", 2, 0); conn.commit(); conn.close()
    assert journal.settle_pending() == 1

    conn = db.connect()
    r = conn.execute("SELECT * FROM predictions_log").fetchone()
    conn.close()
    assert r["status"] == "settled"
    assert (r["actual_home_goals"], r["actual_away_goals"]) == (2, 0)
    assert r["outcome"] == 0 and r["predicted_outcome"] == 0 and r["correct_1x2"] == 1
    assert r["rps"] is not None and r["brier"] is not None
    # Brier = (0.55-1)^2 + (0.25)^2 + (0.20)^2
    assert abs(r["brier"] - ((0.55 - 1) ** 2 + 0.25 ** 2 + 0.20 ** 2)) < 1e-9
    # déjà réglée -> plus rien à régler
    assert journal.settle_pending() == 0


def test_disabled_flag_skips_logging(temp_db, monkeypatch):
    monkeypatch.setattr(config, "PREDICTION_LOG_ENABLED", False)
    conn = db.connect()
    _team(conn, "n_a", "A"); _team(conn, "n_b", "B")
    _fixture(conn, "n_a", "n_b", "2026-06-20")
    conn.commit(); conn.close()
    assert journal.maybe_log(PRED, config.DOMAIN_INTL, "FIFA World Cup", "n_a", "n_b", True, False) is False
