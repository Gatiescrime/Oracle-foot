"""Tests anti-fuite des features.

Principe du test clé : la ligne de features d'un match ne doit dépendre QUE du
passé. Donc si on ajoute des matchs POSTÉRIEURS, la ligne de ce match ne doit pas
bouger d'un iota. On le vérifie sur données synthétiques (rapide, déterministe) et
sur la vraie base (échantillon).
"""

import os

import numpy as np
import pandas as pd
import pytest

from pipeline import config, db, features


def _make_db(matches):
    """Crée une base en mémoire avec quelques matchs clubs synthétiques.

    `matches` : liste de tuples (date, home, away, hg, ag, hxg, axg).
    """
    conn = db.connect(":memory:")
    db.reset_schema(conn)
    teams = set()
    for _, h, a, *_ in matches:
        teams.update([h, a])
    for t in teams:
        db.upsert_team(conn, t, t, "club", "Test")
    sql = ("""INSERT OR REPLACE INTO matches
              (match_id,date,season,competition,domain,home_team_id,away_team_id,
               home_goals,away_goals,neutral,home_shots,away_shots,home_sot,away_sot,
               home_xg,away_xg,odds_home,odds_draw,odds_away,
               odds_avg_home,odds_avg_draw,odds_avg_away)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""")
    for date, h, a, hg, ag, hxg, axg in matches:
        mid = db.make_match_id("club", date, h, a)
        conn.execute(sql, (mid, date, "2023/24", "Premier League", "club", h, a,
                           hg, ag, 0, None, None, None, None, hxg, axg,
                           2.0, 3.0, 4.0, None, None, None))
    conn.commit()
    return conn


# Un petit calendrier : A et B jouent plusieurs fois, dates croissantes.
BASE = [
    ("2023-08-01", "A", "B", 2, 0, 1.8, 0.5),
    ("2023-08-08", "C", "A", 1, 1, 1.0, 1.2),
    ("2023-08-15", "B", "C", 0, 3, 0.4, 2.5),
    ("2023-08-22", "A", "C", 1, 0, 1.1, 0.9),
    ("2023-08-29", "B", "A", 2, 2, 1.5, 1.6),
]
FUTURE = [
    ("2023-09-05", "C", "B", 4, 0, 3.0, 0.3),
    ("2023-09-12", "A", "B", 3, 1, 2.2, 0.8),
]


def test_adding_future_matches_does_not_change_past_rows():
    conn_base = _make_db(BASE)
    feats_base = features.build_features(conn_base, "club")

    conn_full = _make_db(BASE + FUTURE)
    feats_full = features.build_features(conn_full, "club")

    common = feats_base.index
    cols = [c for c in feats_base.columns if c not in ("date",)]
    a = feats_base.loc[common, cols]
    b = feats_full.loc[common, cols]
    # comparaison robuste aux NaN
    pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_first_match_has_no_form_history():
    conn = _make_db(BASE)
    feats = features.build_features(conn, "club")
    first = feats.sort_values("date").iloc[0]
    # première apparition des deux équipes -> forme et repos inconnus
    assert np.isnan(first["home_form5_ppg"])
    assert np.isnan(first["away_form5_ppg"])
    assert np.isnan(first["home_rest_days"])
    assert first["home_played"] == 0


def test_form_values_use_only_past():
    """Vérifie un calcul concret : la forme d'A à son 2e match (08-22 ? non,
    A joue 08-01, 08-08, 08-22) utilise bien ses résultats antérieurs."""
    conn = _make_db(BASE)
    feats = features.build_features(conn, "club").sort_values("date")
    # match A vs C du 22/08 : A a joué 01/08 (2-0) et 08/08 (1-1, à l'extérieur).
    row = feats[(feats["home_team_id"] == "A") & (feats["date"] == pd.Timestamp("2023-08-22"))].iloc[0]
    # buts marqués par A sur ses 2 matchs précédents : 2 puis 1 -> moyenne 1.5
    assert row["home_form5_gf"] == pytest.approx(1.5)
    # buts encaissés : 0 puis 1 -> moyenne 0.5
    assert row["home_form5_ga"] == pytest.approx(0.5)
    # points : victoire (3) puis nul (1) -> 2.0
    assert row["home_form5_ppg"] == pytest.approx(2.0)
    # repos : du 08/08 au 22/08 = 14 jours
    assert row["home_rest_days"] == pytest.approx(14)


def test_xg_rolling_uses_only_past():
    conn = _make_db(BASE)
    feats = features.build_features(conn, "club").sort_values("date")
    row = feats[(feats["home_team_id"] == "A") & (feats["date"] == pd.Timestamp("2023-08-22"))].iloc[0]
    # xG marqués par A : 01/08 -> 1.8 (domicile), 08/08 -> 1.2 (extérieur) -> moy 1.5
    assert row["home_xg5"] == pytest.approx(1.5)


@pytest.mark.skipif(not os.path.exists(config.DB_PATH), reason="base absente")
def test_real_data_no_leak_sample():
    """Sur la vraie base : on tronque le jeu de clubs après un match donné et on
    vérifie que la ligne de ce match est identique au calcul sur tout l'historique."""
    conn = db.connect()
    full = features.build_features(conn, "club").sort_values("date")
    # prend un match au milieu de l'historique
    target = full.iloc[len(full) // 2]
    cutoff = target["date"]

    # reconstruit une base tronquée (matchs <= date de coupe)
    raw = pd.read_sql_query(
        "SELECT * FROM matches WHERE domain='club' AND date <= ? ORDER BY date, match_id",
        conn, params=(cutoff.strftime("%Y-%m-%d"),), parse_dates=["date"])
    tmp = db.connect(":memory:")
    db.reset_schema(tmp)
    for tid in set(raw["home_team_id"]) | set(raw["away_team_id"]):
        db.upsert_team(tmp, tid, tid, "club", None)
    raw_store = raw.copy()
    raw_store["date"] = raw_store["date"].dt.strftime("%Y-%m-%d")
    raw_store.to_sql("matches", tmp, if_exists="append", index=False)
    trunc = features.build_features(tmp, "club")

    cols = [c for c in full.columns if c not in ("date",)]
    a = full.loc[[target.name], cols]
    b = trunc.loc[[target.name], cols]
    pd.testing.assert_frame_equal(a, b, check_dtype=False)
