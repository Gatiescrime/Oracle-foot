"""Schéma SQLite et accès bas niveau à `data/football.db`.

Tables :
  teams        équipes canoniques (un identifiant stable par équipe)
  team_aliases (source, libellé) -> team_id   (la table de correspondance)
  matches      tous les matchs joués, schéma commun clubs + sélections
  fixtures     matchs à venir (calendrier CdM 2026)
  ingest_log   trace de chaque exécution de refresh

L'identifiant de match est un hash déterministe -> rejouer le refresh ne crée
jamais de doublon (INSERT OR REPLACE).
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    team_id        TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    domain         TEXT NOT NULL,
    country        TEXT
);

CREATE TABLE IF NOT EXISTS team_aliases (
    source  TEXT NOT NULL,
    alias   TEXT NOT NULL,
    domain  TEXT NOT NULL,
    team_id TEXT NOT NULL REFERENCES teams(team_id),
    PRIMARY KEY (source, alias, domain)
);

CREATE TABLE IF NOT EXISTS matches (
    match_id      TEXT PRIMARY KEY,
    date          TEXT NOT NULL,
    season        TEXT,
    competition   TEXT NOT NULL,
    domain        TEXT NOT NULL,
    home_team_id  TEXT NOT NULL REFERENCES teams(team_id),
    away_team_id  TEXT NOT NULL REFERENCES teams(team_id),
    home_goals    INTEGER NOT NULL,
    away_goals    INTEGER NOT NULL,
    neutral       INTEGER NOT NULL DEFAULT 0,
    home_shots    REAL, away_shots REAL,
    home_sot      REAL, away_sot   REAL,
    home_xg       REAL, away_xg    REAL,
    -- 1X2 pré-match (prix de décision, sans fuite)
    odds_home     REAL, odds_draw  REAL, odds_away REAL,
    odds_avg_home REAL, odds_avg_draw REAL, odds_avg_away REAL,
    odds_max_home REAL, odds_max_draw REAL, odds_max_away REAL,
    -- O/U 2,5 buts à l'ouverture (pré-match)
    odds_open_over25  REAL, odds_open_under25  REAL,
    -- CLÔTURE : mesure a posteriori (CLV) UNIQUEMENT, jamais en décision
    odds_close_home   REAL, odds_close_draw   REAL, odds_close_away   REAL,
    odds_close_over25 REAL, odds_close_under25 REAL,
    UNIQUE (domain, date, home_team_id, away_team_id)
);

CREATE TABLE IF NOT EXISTS fixtures (
    fixture_id   TEXT PRIMARY KEY,
    date         TEXT NOT NULL,
    competition  TEXT NOT NULL,
    domain       TEXT NOT NULL,
    home_team_id TEXT NOT NULL REFERENCES teams(team_id),
    away_team_id TEXT NOT NULL REFERENCES teams(team_id),
    neutral      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (domain, date, home_team_id, away_team_id)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    source  TEXT,
    run_at  TEXT,
    rows    INTEGER,
    status  TEXT,
    message TEXT
);

CREATE TABLE IF NOT EXISTS news_cache (
    cache_key  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_usage (
    day   TEXT PRIMARY KEY,
    calls INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Phase P4 : trace des meilleurs prix captés en live (suivi de ligne / CLV).
-- Persiste à travers les refresh (jamais réinitialisée) : la CLV s'accumule.
CREATE TABLE IF NOT EXISTS odds_snapshots (
    captured_at  TEXT NOT NULL,
    event_id     TEXT,
    competition  TEXT,
    home         TEXT,
    away         TEXT,
    market       TEXT NOT NULL,
    selection    TEXT NOT NULL,
    best_odds    REAL NOT NULL,
    book         TEXT,
    PRIMARY KEY (event_id, market, selection, captured_at)
);

CREATE TABLE IF NOT EXISTS players (
    understat_id TEXT NOT NULL,
    team_id      TEXT NOT NULL REFERENCES teams(team_id),
    player_name  TEXT NOT NULL,
    season       TEXT NOT NULL,
    competition  TEXT,
    games        INTEGER, minutes INTEGER,
    goals        INTEGER, npg INTEGER,
    xg           REAL, npxg REAL, assists REAL,
    position     TEXT,
    PRIMARY KEY (understat_id, season)
);

-- Buteurs des SÉLECTIONS : agrégat (équipe, joueur) des buts récents + dernier but.
-- Pas de xG ni de minutes côté international (source martj42) : le taux d'un joueur
-- vient de sa PART de buts récents. Estimation marquée « indicative » dans l'API.
CREATE TABLE IF NOT EXISTS intl_scorers (
    team_id     TEXT NOT NULL,
    player_name TEXT NOT NULL,
    goals       INTEGER NOT NULL,
    last_date   TEXT,
    PRIMARY KEY (team_id, player_name)
);

CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date);
CREATE INDEX IF NOT EXISTS idx_matches_domain ON matches(domain);
CREATE INDEX IF NOT EXISTS idx_matches_teams ON matches(home_team_id, away_team_id);
CREATE INDEX IF NOT EXISTS idx_players_team ON players(team_id);
CREATE INDEX IF NOT EXISTS idx_intl_scorers_team ON intl_scorers(team_id);
"""

# Tables auxiliaires PERSISTANTES (jamais réinitialisées par reset_schema) :
# garanties présentes à chaque connexion, même sur une base livrée plus ancienne.
_AUX_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_cache (
    cache_key  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_usage (
    day   TEXT PRIMARY KEY,
    calls INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS odds_snapshots (
    captured_at  TEXT NOT NULL,
    event_id     TEXT,
    competition  TEXT,
    home         TEXT,
    away         TEXT,
    market       TEXT NOT NULL,
    selection    TEXT NOT NULL,
    best_odds    REAL NOT NULL,
    book         TEXT,
    PRIMARY KEY (event_id, market, selection, captured_at)
);
CREATE TABLE IF NOT EXISTS intl_scorers (
    team_id     TEXT NOT NULL,
    player_name TEXT NOT NULL,
    goals       INTEGER NOT NULL,
    last_date   TEXT,
    PRIMARY KEY (team_id, player_name)
);
-- Apprentissage par l'expérience : journal des prédictions (Phase 1).
-- PERSISTANT (jamais réinitialisé) : une prédiction est enregistrée AVANT que le
-- résultat soit connu (statut 'pending'), puis 'réglée' quand le vrai score arrive.
CREATE TABLE IF NOT EXISTS predictions_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    model_version   TEXT NOT NULL,
    domain          TEXT NOT NULL,
    competition     TEXT NOT NULL,
    home_team_id    TEXT NOT NULL,
    away_team_id    TEXT NOT NULL,
    match_date      TEXT NOT NULL,
    neutral         INTEGER NOT NULL DEFAULT 0,
    use_qualitative INTEGER NOT NULL DEFAULT 0,
    p_home REAL NOT NULL, p_draw REAL NOT NULL, p_away REAL NOT NULL,
    exp_home_goals REAL, exp_away_goals REAL,
    ml_home INTEGER, ml_away INTEGER,
    market_home REAL, market_draw REAL, market_away REAL,
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | settled
    settled_at      TEXT,
    actual_home_goals INTEGER, actual_away_goals INTEGER,
    outcome           INTEGER,   -- 0 dom / 1 nul / 2 ext
    predicted_outcome INTEGER,
    correct_1x2       INTEGER,
    brier REAL, rps REAL, clv REAL,
    UNIQUE (model_version, domain, home_team_id, away_team_id, match_date,
            neutral, use_qualitative)
);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    # Idempotent : garantit l'existence des tables auxiliaires persistantes
    # (news_cache, api_usage, app_meta, odds_snapshots) même sur une base livrée
    # avant l'ajout d'une table. N'efface jamais rien (CREATE IF NOT EXISTS).
    try:
        conn.executescript(_AUX_SCHEMA)
    except sqlite3.Error:
        pass
    return conn


def reset_schema(conn: sqlite3.Connection) -> None:
    """Reconstruit la base de zéro (refresh idempotent)."""
    for t in ("ingest_log", "intl_scorers", "players", "fixtures", "matches", "team_aliases", "teams"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.executescript(SCHEMA)
    conn.commit()


def make_match_id(domain: str, date: str, home_id: str, away_id: str) -> str:
    key = f"{domain}|{date}|{home_id}|{away_id}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def upsert_team(conn, team_id, canonical_name, domain, country=None):
    conn.execute(
        "INSERT OR IGNORE INTO teams(team_id, canonical_name, domain, country) VALUES (?,?,?,?)",
        (team_id, canonical_name, domain, country),
    )


def upsert_alias(conn, source, alias, domain, team_id):
    conn.execute(
        "INSERT OR REPLACE INTO team_aliases(source, alias, domain, team_id) VALUES (?,?,?,?)",
        (source, alias, domain, team_id),
    )


def log_run(conn, source, rows, status, message=""):
    conn.execute(
        "INSERT INTO ingest_log(source, run_at, rows, status, message) VALUES (?,?,?,?,?)",
        (source, datetime.now(timezone.utc).isoformat(timespec="seconds"), rows, status, message),
    )
    conn.commit()
