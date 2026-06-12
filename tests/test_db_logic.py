"""Tests hors-ligne du schéma et de la logique de dédoublonnage (base en mémoire)."""

from pipeline import db


def test_make_match_id_deterministic_and_unique():
    a = db.make_match_id("club", "2024-01-01", "c_arsenal", "c_chelsea")
    b = db.make_match_id("club", "2024-01-01", "c_arsenal", "c_chelsea")
    c = db.make_match_id("club", "2024-01-01", "c_chelsea", "c_arsenal")
    assert a == b            # déterministe
    assert a != c            # l'ordre domicile/extérieur compte


def test_reset_schema_and_dedup():
    conn = db.connect(":memory:")
    db.reset_schema(conn)
    db.upsert_team(conn, "c_arsenal", "Arsenal", "club", "Angleterre")
    db.upsert_team(conn, "c_chelsea", "Chelsea", "club", "Angleterre")

    mid = db.make_match_id("club", "2024-01-01", "c_arsenal", "c_chelsea")
    row = (mid, "2024-01-01", "2023/24", "Premier League", "club",
           "c_arsenal", "c_chelsea", 2, 0, 0,
           None, None, None, None, None, None, None, None, None, None, None, None)
    sql = ("""INSERT OR REPLACE INTO matches
              (match_id,date,season,competition,domain,home_team_id,away_team_id,
               home_goals,away_goals,neutral,home_shots,away_shots,home_sot,away_sot,
               home_xg,away_xg,odds_home,odds_draw,odds_away,
               odds_avg_home,odds_avg_draw,odds_avg_away)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""")
    conn.execute(sql, row)
    conn.execute(sql, row)  # ré-insertion : doit remplacer, pas dupliquer
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1


def test_schema_reset_is_idempotent():
    conn = db.connect(":memory:")
    db.reset_schema(conn)
    db.reset_schema(conn)  # rejouable sans erreur
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"teams", "team_aliases", "matches", "fixtures", "ingest_log"} <= tables
