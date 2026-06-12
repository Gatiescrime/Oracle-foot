"""Source CLUBS : football-data.co.uk (résultats + tirs + cotes bookmakers).

Renvoie un DataFrame normalisé (libellés d'équipes BRUTS, résolus plus tard).

COTES — distinction cruciale pour le backtest de PARIS (anti-fuite) :

  * Cotes d'OUVERTURE / pré-match (utilisables comme PRIX D'ENTRÉE, sans fuite) :
      - odds_home/draw/away      : prix de décision 1X2 (Pinnacle ouverture -> Bet365
                                   ouverture -> moyenne). Aucune info postérieure au match.
      - odds_avg_home/draw/away  : moyenne du marché à l'ouverture.
      - odds_max_home/draw/away  : MEILLEUR prix dispo sur le marché (Max*).
      - odds_open_over25/under25 : O/U 2,5 buts à l'ouverture (Bet365 -> moyenne).

  * Cotes de CLÔTURE (mesure A POSTERIORI uniquement — JAMAIS en entrée de décision) :
      - odds_close_home/draw/away : 1X2 clôture (Pinnacle PSC -> Bet365 BC). Sert au CLV
                                    (Closing Line Value), l'indicateur d'edge réel.
      - odds_close_over25/under25 : O/U 2,5 buts clôture (Pinnacle PSC).

Règle d'or : les cotes de clôture (PSC*, B365C*) ne servent qu'à MESURER (CLV), jamais
à décider d'un pari — sinon fuite (la clôture intègre de l'info postérieure à l'ouverture).
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from .. import config, http

log = logging.getLogger("pipeline.football_data")


def _first_available(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Renvoie la première colonne existante parmi `cols`, sinon des NaN."""
    for c in cols:
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce")
    return pd.Series([pd.NA] * len(df), index=df.index, dtype="float64")


def fetch_league_season(league: dict, season: dict, use_cache: bool = True) -> pd.DataFrame:
    url = f"{config.FD_BASE_URL}/{season['fd']}/{league['fd']}.csv"
    raw = http.fetch(url, cache_key=f"fd:{league['fd']}:{season['fd']}", use_cache=use_cache)
    df = pd.read_csv(io.BytesIO(raw), encoding="latin-1")
    if "FTHG" not in df.columns or "HomeTeam" not in df.columns:
        log.warning("colonnes inattendues pour %s %s", league["fd"], season["fd"])
        return pd.DataFrame()
    df = df[df["FTHG"].notna() & df["FTAG"].notna()].copy()

    out = pd.DataFrame({
        "date": pd.to_datetime(df["Date"], dayfirst=True, errors="coerce"),
        "home_team": df["HomeTeam"].astype(str).str.strip(),
        "away_team": df["AwayTeam"].astype(str).str.strip(),
        "home_goals": pd.to_numeric(df["FTHG"], errors="coerce"),
        "away_goals": pd.to_numeric(df["FTAG"], errors="coerce"),
        "competition": league["competition"],
        "season": season["label"],
        "neutral": False,
        "home_shots": _first_available(df, ["HS"]),
        "away_shots": _first_available(df, ["AS"]),
        "home_sot": _first_available(df, ["HST"]),
        "away_sot": _first_available(df, ["AST"]),
        # --- 1X2 PRÉ-MATCH (prix de décision, sans fuite) ---
        # Pinnacle ouverture -> Bet365 ouverture -> moyenne ouverture
        "odds_home": _first_available(df, ["PSH", "B365H", "AvgH", "BbAvH"]),
        "odds_draw": _first_available(df, ["PSD", "B365D", "AvgD", "BbAvD"]),
        "odds_away": _first_available(df, ["PSA", "B365A", "AvgA", "BbAvA"]),
        # moyenne du marché à l'ouverture
        "odds_avg_home": _first_available(df, ["AvgH", "BbAvH"]),
        "odds_avg_draw": _first_available(df, ["AvgD", "BbAvD"]),
        "odds_avg_away": _first_available(df, ["AvgA", "BbAvA"]),
        # meilleur prix dispo sur le marché (best price)
        "odds_max_home": _first_available(df, ["MaxH", "BbMxH"]),
        "odds_max_draw": _first_available(df, ["MaxD", "BbMxD"]),
        "odds_max_away": _first_available(df, ["MaxA", "BbMxA"]),
        # O/U 2,5 buts à l'ouverture
        "odds_open_over25": _first_available(df, ["B365>2.5", "Avg>2.5", "BbAv>2.5"]),
        "odds_open_under25": _first_available(df, ["B365<2.5", "Avg<2.5", "BbAv<2.5"]),
        # --- CLÔTURE (CLV uniquement, JAMAIS en décision) ---
        "odds_close_home": _first_available(df, ["PSCH", "B365CH"]),
        "odds_close_draw": _first_available(df, ["PSCD", "B365CD"]),
        "odds_close_away": _first_available(df, ["PSCA", "B365CA"]),
        "odds_close_over25": _first_available(df, ["PSC>2.5", "Avg C>2.5", "AvgC>2.5"]),
        "odds_close_under25": _first_available(df, ["PSC<2.5", "Avg C<2.5", "AvgC<2.5"]),
    })
    out = out.dropna(subset=["date", "home_goals", "away_goals"])
    out["home_goals"] = out["home_goals"].astype(int)
    out["away_goals"] = out["away_goals"].astype(int)
    return out


def fetch_all(use_cache: bool = True) -> pd.DataFrame:
    frames = []
    for league in config.CLUB_LEAGUES:
        for season in config.CLUB_SEASONS:
            try:
                df = fetch_league_season(league, season, use_cache=use_cache)
            except Exception as e:  # noqa: BLE001
                log.error("échec %s %s: %s", league["competition"], season["label"], e)
                continue
            if not df.empty:
                frames.append(df)
                log.info("%s %s : %d matchs", league["competition"], season["label"], len(df))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)
