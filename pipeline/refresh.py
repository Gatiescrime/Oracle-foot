"""Orchestrateur d'ingestion : reconstruit `data/football.db` en une commande.

    python -m pipeline.refresh            # avec cache (rapide)
    python -m pipeline.refresh --no-cache # force le re-téléchargement

Étapes : (1) clubs football-data -> équipes + matchs + cotes, (2) xG understat
rattaché aux matchs clubs, (3) sélections martj42 -> matchs, (4) calendrier à venir.
Le refresh est idempotent : il repart d'un schéma neuf à chaque exécution.
"""

from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from . import config, db, names
from .sources import football_data, martj42, understat

log = logging.getLogger("pipeline.refresh")

_COMP_COUNTRY = {l["competition"]: l["country"] for l in config.CLUB_LEAGUES}


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(config.LOG_PATH)],
    )


def tid(name: str, domain: str) -> str:
    """Identifiant canonique d'équipe, préfixé par domaine pour éviter toute collision."""
    prefix = "c_" if domain == config.DOMAIN_CLUB else "n_"
    return prefix + names.slugify(name)


# ---------------------------------------------------------------------------
def ingest_clubs(conn, df: pd.DataFrame) -> int:
    domain = config.DOMAIN_CLUB
    rows = []
    for r in df.itertuples(index=False):
        hid, aid = tid(r.home_team, domain), tid(r.away_team, domain)
        country = _COMP_COUNTRY.get(r.competition)
        db.upsert_team(conn, hid, r.home_team, domain, country)
        db.upsert_team(conn, aid, r.away_team, domain, country)
        db.upsert_alias(conn, "football-data", r.home_team, domain, hid)
        db.upsert_alias(conn, "football-data", r.away_team, domain, aid)
        date = r.date.strftime("%Y-%m-%d")
        rows.append((
            db.make_match_id(domain, date, hid, aid), date, r.season, r.competition, domain,
            hid, aid, int(r.home_goals), int(r.away_goals), 0,
            _num(r.home_shots), _num(r.away_shots), _num(r.home_sot), _num(r.away_sot),
            None, None,  # xg rattaché ensuite
            _num(r.odds_home), _num(r.odds_draw), _num(r.odds_away),
            _num(r.odds_avg_home), _num(r.odds_avg_draw), _num(r.odds_avg_away),
            _num(r.odds_max_home), _num(r.odds_max_draw), _num(r.odds_max_away),
            _num(r.odds_open_over25), _num(r.odds_open_under25),
            _num(r.odds_close_home), _num(r.odds_close_draw), _num(r.odds_close_away),
            _num(r.odds_close_over25), _num(r.odds_close_under25),
        ))
    conn.executemany(
        """INSERT OR REPLACE INTO matches
           (match_id,date,season,competition,domain,home_team_id,away_team_id,
            home_goals,away_goals,neutral,home_shots,away_shots,home_sot,away_sot,
            home_xg,away_xg,odds_home,odds_draw,odds_away,
            odds_avg_home,odds_avg_draw,odds_avg_away,
            odds_max_home,odds_max_draw,odds_max_away,
            odds_open_over25,odds_open_under25,
            odds_close_home,odds_close_draw,odds_close_away,
            odds_close_over25,odds_close_under25)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def attach_xg(conn, df_fd: pd.DataFrame, df_und: pd.DataFrame) -> int:
    """Rattache le xG understat aux matchs clubs déjà insérés.

    Apparie les noms understat aux noms football-data par ligue/saison, puis joint
    sur (équipe domicile, équipe extérieur) avec une tolérance de date de ±2 jours.
    """
    domain = config.DOMAIN_CLUB
    updated = 0
    for (comp, season), und_grp in df_und.groupby(["competition", "season"]):
        fd_grp = df_fd[(df_fd["competition"] == comp) & (df_fd["season"] == season)]
        if fd_grp.empty:
            continue
        fd_names = sorted(set(fd_grp["home_team"]) | set(fd_grp["away_team"]))
        und_names = sorted(set(und_grp["home_team"]) | set(und_grp["away_team"]))
        mapping = names.match_sets(und_names, fd_names)
        for u, c in mapping.items():
            db.upsert_alias(conn, "understat", u, domain, tid(c, domain))

        # index des matchs clubs : (hid, aid) -> [(date, match_id)]
        index: dict[tuple[str, str], list[tuple[pd.Timestamp, str]]] = {}
        for r in fd_grp.itertuples(index=False):
            hid, aid = tid(r.home_team, domain), tid(r.away_team, domain)
            mid = db.make_match_id(domain, r.date.strftime("%Y-%m-%d"), hid, aid)
            index.setdefault((hid, aid), []).append((r.date, mid))

        for r in und_grp.itertuples(index=False):
            hid = tid(mapping.get(r.home_team, r.home_team), domain)
            aid = tid(mapping.get(r.away_team, r.away_team), domain)
            cands = index.get((hid, aid))
            if not cands:
                continue
            best = min(cands, key=lambda dm: abs((dm[0] - r.date).days))
            if abs((best[0] - r.date).days) > 2:
                continue
            conn.execute("UPDATE matches SET home_xg=?, away_xg=? WHERE match_id=?",
                         (float(r.home_xg), float(r.away_xg), best[1]))
            updated += 1
    conn.commit()
    return updated


def ingest_players(conn, df: pd.DataFrame) -> int:
    """Insère les totaux par joueur (clubs), en résolvant l'équipe understat -> team_id.

    On réutilise les alias understat déjà posés par `attach_xg` (même libellé que
    dans les matchs). Faute d'alias, on retombe sur l'identifiant slugifié.
    """
    domain = config.DOMAIN_CLUB
    alias_rows = conn.execute(
        "SELECT alias, team_id FROM team_aliases WHERE source='understat' AND domain=?",
        (domain,)).fetchall()
    alias_map = {a["alias"]: a["team_id"] for a in alias_rows}
    known = {r["team_id"] for r in conn.execute("SELECT team_id FROM teams").fetchall()}

    rows = []
    for r in df.itertuples(index=False):
        team_id = alias_map.get(r.team_title) or tid(r.team_title, domain)
        if team_id not in known:
            # équipe sans match en base (promue/reléguée hors fenêtre) : on saute
            continue
        rows.append((
            r.understat_id, team_id, r.player_name, r.season, r.competition,
            int(r.games), int(r.minutes), int(r.goals), int(r.npg),
            float(r.xg), float(r.npxg), float(r.assists), r.position,
        ))
    conn.executemany(
        """INSERT OR REPLACE INTO players
           (understat_id, team_id, player_name, season, competition,
            games, minutes, goals, npg, xg, npxg, assists, position)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def ingest_internationals(conn, df: pd.DataFrame) -> int:
    domain = config.DOMAIN_INTL
    rows = []
    for r in df.itertuples(index=False):
        hid, aid = tid(r.home_team, domain), tid(r.away_team, domain)
        db.upsert_team(conn, hid, r.home_team, domain, None)
        db.upsert_team(conn, aid, r.away_team, domain, None)
        db.upsert_alias(conn, "martj42", r.home_team, domain, hid)
        db.upsert_alias(conn, "martj42", r.away_team, domain, aid)
        date = r.date.strftime("%Y-%m-%d")
        rows.append((
            db.make_match_id(domain, date, hid, aid), date, r.season, r.competition, domain,
            hid, aid, int(r.home_goals), int(r.away_goals), 1 if r.neutral else 0,
            None, None, None, None, None, None,  # shots/sot/xg
            None, None, None, None, None, None,  # odds 1X2 + avg
            None, None, None,                    # odds max
            None, None,                          # O/U ouverture
            None, None, None,                    # 1X2 clôture
            None, None,                          # O/U clôture
        ))
    conn.executemany(
        """INSERT OR REPLACE INTO matches
           (match_id,date,season,competition,domain,home_team_id,away_team_id,
            home_goals,away_goals,neutral,home_shots,away_shots,home_sot,away_sot,
            home_xg,away_xg,odds_home,odds_draw,odds_away,
            odds_avg_home,odds_avg_draw,odds_avg_away,
            odds_max_home,odds_max_draw,odds_max_away,
            odds_open_over25,odds_open_under25,
            odds_close_home,odds_close_draw,odds_close_away,
            odds_close_over25,odds_close_under25)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def ingest_fixtures(conn, df: pd.DataFrame) -> int:
    domain = config.DOMAIN_INTL
    rows = []
    for r in df.itertuples(index=False):
        hid, aid = tid(r.home_team, domain), tid(r.away_team, domain)
        # certaines sélections n'apparaissent que dans le calendrier futur
        db.upsert_team(conn, hid, r.home_team, domain, None)
        db.upsert_team(conn, aid, r.away_team, domain, None)
        db.upsert_alias(conn, "martj42", r.home_team, domain, hid)
        db.upsert_alias(conn, "martj42", r.away_team, domain, aid)
        date = r.date.strftime("%Y-%m-%d")
        rows.append((
            db.make_match_id(domain, date, hid, aid), date, r.competition, domain,
            hid, aid, 1 if r.neutral else 0,
        ))
    conn.executemany(
        """INSERT OR REPLACE INTO fixtures
           (fixture_id,date,competition,domain,home_team_id,away_team_id,neutral)
           VALUES (?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def _num(v):
    """Convertit en float Python ou None (SQLite n'aime pas les NA pandas)."""
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return None
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def refresh(use_cache: bool = True) -> dict:
    conn = db.connect()
    db.reset_schema(conn)
    summary = {}

    log.info("== CLUBS (football-data.co.uk) ==")
    df_fd = football_data.fetch_all(use_cache=use_cache)
    n_clubs = ingest_clubs(conn, df_fd) if not df_fd.empty else 0
    db.log_run(conn, "football-data", n_clubs, "ok" if n_clubs else "vide")
    summary["clubs"] = n_clubs

    log.info("== xG (understat.com) ==")
    try:
        df_und = understat.fetch_all(use_cache=use_cache)
        n_xg = attach_xg(conn, df_fd, df_und) if not df_und.empty else 0
        db.log_run(conn, "understat", n_xg, "ok" if n_xg else "vide")
    except Exception as e:  # noqa: BLE001
        log.error("understat indisponible: %s", e)
        db.log_run(conn, "understat", 0, "erreur", str(e))
        n_xg = 0
    summary["xg_attached"] = n_xg

    log.info("== JOUEURS (understat.com) ==")
    try:
        df_players = understat.fetch_players_all(use_cache=use_cache)
        n_players = ingest_players(conn, df_players) if not df_players.empty else 0
        db.log_run(conn, "understat-players", n_players, "ok" if n_players else "vide")
    except Exception as e:  # noqa: BLE001
        log.error("joueurs understat indisponibles: %s", e)
        db.log_run(conn, "understat-players", 0, "erreur", str(e))
        n_players = 0
    summary["players"] = n_players

    log.info("== SÉLECTIONS (martj42) ==")
    df_intl, df_fix = martj42.fetch_all(use_cache=use_cache)
    n_intl = ingest_internationals(conn, df_intl) if not df_intl.empty else 0
    db.log_run(conn, "martj42", n_intl, "ok" if n_intl else "vide")
    n_fix = ingest_fixtures(conn, df_fix) if not df_fix.empty else 0
    db.log_run(conn, "fixtures", n_fix, "ok" if n_fix else "vide")
    summary["internationals"] = n_intl
    summary["fixtures"] = n_fix

    n_teams = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    summary["teams"] = n_teams
    conn.close()

    log.info("== TERMINÉ == clubs=%(clubs)d xG=%(xg_attached)d joueurs=%(players)d "
             "intl=%(internationals)d fixtures=%(fixtures)d équipes=%(teams)d", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Reconstruit la base de données football.")
    parser.add_argument("--no-cache", action="store_true", help="force le re-téléchargement")
    args = parser.parse_args()
    _setup_logging()
    refresh(use_cache=not args.no_cache)


if __name__ == "__main__":
    main()
