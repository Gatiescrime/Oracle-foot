"""PHASE A — Mode Rapide : ne re-télécharger QUE la saison clubs en cours.

Les saisons passées sont immuables : en Rapide on les sert depuis le cache disque
(TTL long) et on ne re-télécharge que la saison courante. On vérifie la logique de
sélection cache/réseau sans jamais toucher au réseau.
"""

import pandas as pd

from pipeline import config
from pipeline.sources import football_data


def _record_calls(monkeypatch):
    calls = []

    def fake_fetch_league_season(league, season, use_cache=True, ttl_hours=None):
        calls.append({"fd": season["fd"], "use_cache": use_cache, "ttl_hours": ttl_hours})
        return pd.DataFrame()        # vide : on ne teste que la stratégie d'accès

    monkeypatch.setattr(football_data, "fetch_league_season", fake_fetch_league_season)
    return calls


def test_quick_refreshes_only_current_season(monkeypatch):
    calls = _record_calls(monkeypatch)
    current = config.CLUB_SEASONS[-1]["fd"]

    football_data.fetch_all(use_cache=False, quick=True)

    for c in calls:
        if c["fd"] == current:
            assert c["use_cache"] is False        # saison en cours : re-téléchargée
            assert c["ttl_hours"] is None
        else:
            assert c["use_cache"] is True         # saison passée : cache disque
            assert c["ttl_hours"] == config.QUICK_HEAVY_TTL_HOURS

    # une seule saison courante re-téléchargée par ligue
    n_current = sum(1 for c in calls if c["fd"] == current and not c["use_cache"])
    assert n_current == len(config.CLUB_LEAGUES)


def test_full_mode_downloads_all_seasons(monkeypatch):
    calls = _record_calls(monkeypatch)
    football_data.fetch_all(use_cache=False, quick=False)
    # mode complet : aucune réutilisation forcée du cache, toutes saisons en réseau
    assert calls and all(c["use_cache"] is False and c["ttl_hours"] is None for c in calls)
    assert len(calls) == len(config.CLUB_LEAGUES) * len(config.CLUB_SEASONS)
