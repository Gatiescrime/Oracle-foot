"""
Ingestion des données réelles de football.

Deux univers de données :
  - CLUBS : résultats + cotes depuis football-data.co.uk (grandes ligues européennes)
  - SELECTIONS : résultats internationaux 1872 -> présent (dataset martj42)

Le module télécharge, nettoie et normalise les données dans un schéma commun :
    date, home_team, away_team, home_goals, away_goals, competition, season, neutral

Utilisation :
    python pipeline/ingest.py
"""

from __future__ import annotations

import io
import os
import sys
import time
import urllib.request
from datetime import datetime

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Configuration des ligues de clubs (football-data.co.uk)
# Code division -> (nom lisible, pays)
# ---------------------------------------------------------------------------
CLUB_LEAGUES = {
    "E0": ("Premier League", "Angleterre"),
    "SP1": ("La Liga", "Espagne"),
    "D1": ("Bundesliga", "Allemagne"),
    "I1": ("Serie A", "Italie"),
    "F1": ("Ligue 1", "France"),
}

# Saisons à télécharger (codes football-data : 2021 = saison 2021/22)
CLUB_SEASONS = ["2122", "2223", "2324", "2425", "2526"]

INTL_RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"


def _download(url: str, retries: int = 3, timeout: int = 40) -> bytes:
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (football-model)"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Echec du téléchargement {url}: {last_err}")


def ingest_clubs() -> pd.DataFrame:
    """Télécharge et normalise les résultats de clubs."""
    frames = []
    for code, (league_name, country) in CLUB_LEAGUES.items():
        for season in CLUB_SEASONS:
            url = f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
            try:
                raw = _download(url)
            except RuntimeError as e:
                print(f"  ! {league_name} {season}: {e}", file=sys.stderr)
                continue
            df = pd.read_csv(io.BytesIO(raw), encoding="latin-1")
            if "FTHG" not in df.columns:
                continue
            df = df[df["FTHG"].notna() & df["FTAG"].notna()].copy()
            season_label = f"20{season[:2]}/{season[2:]}"
            out = pd.DataFrame({
                "date": pd.to_datetime(df["Date"], dayfirst=True, errors="coerce"),
                "home_team": df["HomeTeam"].astype(str).str.strip(),
                "away_team": df["AwayTeam"].astype(str).str.strip(),
                "home_goals": pd.to_numeric(df["FTHG"], errors="coerce"),
                "away_goals": pd.to_numeric(df["FTAG"], errors="coerce"),
                "competition": league_name,
                "season": season_label,
                "neutral": False,
            })
            # Stats avancées si dispo (tirs cadrés) -> utiles plus tard
            for src, dst in [("HST", "home_sot"), ("AST", "away_sot"),
                             ("HS", "home_shots"), ("AS", "away_shots")]:
                out[dst] = pd.to_numeric(df[src], errors="coerce") if src in df.columns else pd.NA
            frames.append(out)
            print(f"  + {league_name} {season_label}: {len(out)} matchs")
            time.sleep(0.3)
    if not frames:
        return pd.DataFrame()
    clubs = pd.concat(frames, ignore_index=True)
    clubs = clubs.dropna(subset=["date", "home_goals", "away_goals"])
    clubs["home_goals"] = clubs["home_goals"].astype(int)
    clubs["away_goals"] = clubs["away_goals"].astype(int)
    clubs = clubs.sort_values("date").reset_index(drop=True)
    return clubs


def ingest_internationals() -> pd.DataFrame:
    """Télécharge et normalise les résultats internationaux (sélections)."""
    raw = _download(INTL_RESULTS_URL)
    df = pd.read_csv(io.BytesIO(raw))
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    # Matchs joués uniquement (scores renseignés) pour l'entraînement
    played = df[df["home_score"].notna() & df["away_score"].notna()].copy()
    out = pd.DataFrame({
        "date": played["date"],
        "home_team": played["home_team"].astype(str).str.strip(),
        "away_team": played["away_team"].astype(str).str.strip(),
        "home_goals": pd.to_numeric(played["home_score"], errors="coerce"),
        "away_goals": pd.to_numeric(played["away_score"], errors="coerce"),
        "competition": played["tournament"].astype(str).str.strip(),
        "season": played["date"].dt.year.astype("Int64").astype(str),
        "neutral": played["neutral"].astype(str).str.upper().eq("TRUE"),
    })
    out = out.dropna(subset=["date", "home_goals", "away_goals"])
    out["home_goals"] = out["home_goals"].astype(int)
    out["away_goals"] = out["away_goals"].astype(int)
    out = out.sort_values("date").reset_index(drop=True)

    # Calendrier futur (matchs à venir, scores vides) -> utile pour prédire la CdM 2026
    upcoming = df[df["home_score"].isna() & df["date"].notna() & (df["date"] >= pd.Timestamp.today().normalize())].copy()
    fixtures = pd.DataFrame({
        "date": upcoming["date"],
        "home_team": upcoming["home_team"].astype(str).str.strip(),
        "away_team": upcoming["away_team"].astype(str).str.strip(),
        "competition": upcoming["tournament"].astype(str).str.strip(),
        "neutral": upcoming["neutral"].astype(str).str.upper().eq("TRUE"),
    }).sort_values("date").reset_index(drop=True)
    fixtures.to_parquet(os.path.join(DATA_DIR, "fixtures_intl.parquet"))
    print(f"  + Calendrier à venir : {len(fixtures)} matchs (dont CdM 2026)")

    return out


def main():
    print("== Ingestion CLUBS (football-data.co.uk) ==")
    clubs = ingest_clubs()
    if not clubs.empty:
        clubs.to_parquet(os.path.join(DATA_DIR, "matches_clubs.parquet"))
        print(f"  => {len(clubs)} matchs clubs sauvegardés\n")

    print("== Ingestion SELECTIONS (martj42/international_results) ==")
    intl = ingest_internationals()
    intl.to_parquet(os.path.join(DATA_DIR, "matches_intl.parquet"))
    print(f"  => {len(intl)} matchs internationaux sauvegardés")
    print(f"\nDonnées écrites dans {DATA_DIR}")


if __name__ == "__main__":
    main()
