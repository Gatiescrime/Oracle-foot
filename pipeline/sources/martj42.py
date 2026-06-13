"""Source SÉLECTIONS : martj42/international_results (GitHub).

Un seul CSV contient l'historique 1872 -> présent ET le calendrier à venir
(scores vides), dont la Coupe du Monde 2026. On en tire :
  - les matchs joués (pour l'entraînement),
  - les matchs à venir (fixtures).

Les libellés de sélections sont déjà propres -> ils servent de noms canoniques.
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from .. import config, http

log = logging.getLogger("pipeline.martj42")


def fetch_all(use_cache: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = http.fetch(config.INTL_RESULTS_URL, cache_key="martj42:results", use_cache=use_cache)
    df = pd.read_csv(io.BytesIO(raw))
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    played = df[df["home_score"].notna() & df["away_score"].notna() & df["date"].notna()].copy()
    results = pd.DataFrame({
        "date": played["date"],
        "home_team": played["home_team"].astype(str).str.strip(),
        "away_team": played["away_team"].astype(str).str.strip(),
        "home_goals": pd.to_numeric(played["home_score"], errors="coerce"),
        "away_goals": pd.to_numeric(played["away_score"], errors="coerce"),
        "competition": played["tournament"].astype(str).str.strip(),
        "season": played["date"].dt.year.astype("Int64").astype(str),
        "neutral": played["neutral"].astype(str).str.upper().eq("TRUE"),
    }).dropna(subset=["home_goals", "away_goals"])
    results["home_goals"] = results["home_goals"].astype(int)
    results["away_goals"] = results["away_goals"].astype(int)
    results = results.sort_values("date").reset_index(drop=True)

    today = pd.Timestamp.today().normalize()
    upcoming = df[df["home_score"].isna() & df["date"].notna() & (df["date"] >= today)].copy()
    fixtures = pd.DataFrame({
        "date": upcoming["date"],
        "home_team": upcoming["home_team"].astype(str).str.strip(),
        "away_team": upcoming["away_team"].astype(str).str.strip(),
        "competition": upcoming["tournament"].astype(str).str.strip(),
        "neutral": upcoming["neutral"].astype(str).str.upper().eq("TRUE"),
    }).sort_values("date").reset_index(drop=True)

    log.info("sélections : %d matchs joués, %d à venir", len(results), len(fixtures))
    return results, fixtures


def fetch_goalscorers(use_cache: bool = True) -> pd.DataFrame:
    """Buteurs des sélections, agrégés sur une fenêtre RÉCENTE par (équipe, joueur).

    Le CSV goalscorers.csv (colonnes date,home_team,away_team,team,scorer,minute,
    own_goal,penalty) liste chaque but de l'histoire internationale. On ne garde que
    les buts récents (>= INTL_SCORER_SINCE_YEAR) pour refléter l'effectif actif, on
    EXCLUT les buts contre son camp (own_goal=TRUE), puis on compte par buteur et on
    note la date du dernier but. Les libellés d'équipes sont ceux de results.csv -> ils
    s'alignent directement sur nos noms canoniques de sélections.

    Renvoie un DataFrame {team, scorer, goals, last_date} (vide si source indisponible).
    """
    raw = http.fetch(config.INTL_SCORERS_URL, cache_key="martj42:goalscorers", use_cache=use_cache)
    df = pd.read_csv(io.BytesIO(raw))
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    own = df["own_goal"].astype(str).str.upper().eq("TRUE")
    keep = df[
        df["date"].notna()
        & df["scorer"].notna()
        & ~own
        & (df["date"].dt.year >= config.INTL_SCORER_SINCE_YEAR)
    ].copy()
    keep["team"] = keep["team"].astype(str).str.strip()
    keep["scorer"] = keep["scorer"].astype(str).str.strip()
    keep = keep[(keep["team"] != "") & (keep["scorer"] != "")]

    agg = (
        keep.groupby(["team", "scorer"])
        .agg(goals=("scorer", "size"), last_date=("date", "max"))
        .reset_index()
    )
    agg["last_date"] = agg["last_date"].dt.strftime("%Y-%m-%d")
    agg = agg.sort_values(["team", "goals"], ascending=[True, False]).reset_index(drop=True)
    log.info("buteurs sélections : %d couples (équipe, joueur) depuis %d",
             len(agg), config.INTL_SCORER_SINCE_YEAR)
    return agg
