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
