"""Source xG CLUBS : understat.com.

understat ne met plus ses données dans le HTML : elles sont servies par l'endpoint
JSON `getLeagueData/{ligue}/{saison}` (réponse compressée). On y récupère, par match,
le xG domicile/extérieur que l'on rattachera ensuite aux matchs football-data.

Renvoie un DataFrame : date, home_team, away_team (libellés understat bruts),
home_goals, away_goals, home_xg, away_xg, competition, season.
"""

from __future__ import annotations

import html
import json
import logging

import pandas as pd

from .. import config, http

log = logging.getLogger("pipeline.understat")

_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def fetch_league_season(league: dict, season: dict, use_cache: bool = True,
                        ttl_hours: float | None = None) -> pd.DataFrame:
    url = config.UNDERSTAT_URL.format(league=league["understat"], season=season["understat"])
    raw = http.fetch(
        url,
        cache_key=f"understat:{league['understat']}:{season['understat']}",
        headers={**_HEADERS, "Referer": f"https://understat.com/league/{league['understat']}/{season['understat']}"},
        use_cache=use_cache, ttl_hours=ttl_hours,
    )
    data = json.loads(raw)
    rows = []
    for m in data.get("dates", []):
        if not m.get("isResult"):
            continue
        try:
            rows.append({
                "date": pd.to_datetime(m["datetime"]).normalize(),
                "home_team": m["h"]["title"],
                "away_team": m["a"]["title"],
                "home_goals": int(m["goals"]["h"]),
                "away_goals": int(m["goals"]["a"]),
                "home_xg": float(m["xG"]["h"]),
                "away_xg": float(m["xG"]["a"]),
                "competition": league["competition"],
                "season": season["label"],
            })
        except (KeyError, ValueError, TypeError) as e:
            log.warning("match understat ignoré (%s): %s", e, m.get("id"))
    df = pd.DataFrame(rows)
    if not df.empty:
        log.info("%s %s : %d matchs xG", league["competition"], season["label"], len(df))
    return df


def fetch_all(use_cache: bool = True, ttl_hours: float | None = None) -> pd.DataFrame:
    frames = []
    for league in config.CLUB_LEAGUES:
        for season in config.CLUB_SEASONS:
            try:
                df = fetch_league_season(league, season, use_cache=use_cache, ttl_hours=ttl_hours)
            except Exception as e:  # noqa: BLE001
                log.error("échec understat %s %s: %s", league["competition"], season["label"], e)
                continue
            if not df.empty:
                frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Données JOUEUR (buteurs) : le même endpoint getLeagueData renvoie, en plus des
# matchs, une liste `players` (totaux de la saison) avec buts, xG, minutes, poste.
# ---------------------------------------------------------------------------
def fetch_league_players(league: dict, season: dict, use_cache: bool = True,
                         ttl_hours: float | None = None) -> pd.DataFrame:
    """Renvoie les totaux par joueur d'une ligue/saison.

    Colonnes : understat_id, player_name, team_title (libellé understat), competition,
    season, games, minutes, goals, npg, xg, npxg, assists, position.
    """
    url = config.UNDERSTAT_URL.format(league=league["understat"], season=season["understat"])
    raw = http.fetch(
        url,
        cache_key=f"understat:{league['understat']}:{season['understat']}",
        headers={**_HEADERS, "Referer": f"https://understat.com/league/{league['understat']}/{season['understat']}"},
        use_cache=use_cache, ttl_hours=ttl_hours,
    )
    data = json.loads(raw)
    rows = []
    for p in data.get("players", []):
        try:
            rows.append({
                "understat_id": str(p["id"]),
                "player_name": html.unescape(p["player_name"]),
                "team_title": html.unescape(p["team_title"]),
                "competition": league["competition"],
                "season": season["label"],
                "games": int(p.get("games") or 0),
                "minutes": int(p.get("time") or 0),
                "goals": int(p.get("goals") or 0),
                "npg": int(p.get("npg") or 0),
                "xg": float(p.get("xG") or 0.0),
                "npxg": float(p.get("npxG") or 0.0),
                "assists": float(p.get("assists") or 0.0),
                "position": p.get("position") or "",
            })
        except (KeyError, ValueError, TypeError) as e:
            log.warning("joueur understat ignoré (%s): %s", e, p.get("id"))
    df = pd.DataFrame(rows)
    if not df.empty:
        log.info("%s %s : %d joueurs", league["competition"], season["label"], len(df))
    return df


def fetch_players_all(use_cache: bool = True, ttl_hours: float | None = None) -> pd.DataFrame:
    frames = []
    for league in config.CLUB_LEAGUES:
        for season in config.CLUB_SEASONS:
            try:
                df = fetch_league_players(league, season, use_cache=use_cache, ttl_hours=ttl_hours)
            except Exception as e:  # noqa: BLE001
                log.error("échec joueurs understat %s %s: %s",
                          league["competition"], season["label"], e)
                continue
            if not df.empty:
                frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
