"""Tests Phase P6 : contexte (qualité de tir, congestion, déplacement, météo).

Tout est hors ligne et déterministe (la météo réseau est remplacée par un stub).
On vérifie :
  - la géométrie (haversine) et l'appariement des lieux (exact + flou + inconnu) ;
  - la congestion = matchs récents PASSÉS uniquement (anti-fuite) ;
  - la distance de déplacement (lieu précédent -> lieu courant ; NaN si inconnu) ;
  - les proxys de qualité de tir glissants (xG/tir) construits sur le seul passé ;
  - le repli météo (désactivée -> rien ; mapping correct via stub) ;
  - le branchement (gating) des features P6 derrière le drapeau de config.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline import config, context, db, features, weather, xgb_model


# --- géométrie + appariement des lieux --------------------------------------
def test_haversine_known_distance():
    # Madrid -> Barcelone ~ 505 km (tolérance large).
    d = context.haversine_km(40.45, -3.69, 41.38, 2.12)
    assert 480 < d < 530
    # même point -> 0
    assert context.haversine_km(40.45, -3.69, 40.45, -3.69) == pytest.approx(0.0)


def test_read_venues_ignores_comments_and_bad_rows():
    text = (
        "# coordonnées\n"
        "team,lat,lon\n"
        "Arsenal,51.555,-0.108\n"
        "\n"
        "# commentaire\n"
        "Bad,notnum,1.0\n"
        "Outofrange,200.0,0.0\n"
        "Real Madrid,40.453,-3.688\n"
    )
    coords = context._read_csv_text(text)
    assert coords == {"Arsenal": (51.555, -0.108), "Real Madrid": (40.453, -3.688)}


def test_align_venue_exact_and_fuzzy_and_unknown():
    teams = {"c_arsenal": "Arsenal", "c_real_madrid": "Real Madrid"}
    raw = {"Arsenal": (51.5, -0.1), "Real Madrid CF": (40.4, -3.7),
           "Club Inconnu XYZ": (0.0, 0.0)}
    aligned = context._align_to_teams(raw, teams)
    assert aligned["c_arsenal"] == (51.5, -0.1)
    assert aligned["c_real_madrid"] == (40.4, -3.7)   # flou (préfixe)
    assert len(aligned) == 2                            # l'inconnu est rejeté


# --- congestion + déplacement (helpers purs) --------------------------------
def test_congestion_counts_only_recent_past():
    now = pd.Timestamp("2023-09-20")
    dates = [pd.Timestamp("2023-09-01"),   # > 14 j -> hors fenêtre
             pd.Timestamp("2023-09-10"),   # dans 14 j
             pd.Timestamp("2023-09-15")]   # dans 14 j
    assert features._congestion(dates, now) == 2.0
    assert features._congestion(None, now) == 0.0   # aucune date -> 0, pas NaN


def test_travel_nan_when_venue_unknown():
    assert np.isnan(features._travel(None, (40.0, -3.0)))
    assert np.isnan(features._travel((40.0, -3.0), None))
    d = features._travel((40.45, -3.69), (41.38, 2.12))
    assert 480 < d < 530


# --- météo : repli + mapping (stub réseau) ----------------------------------
def test_weather_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "WEATHER_ENABLED", False)
    rows = [{"match_id": "m1", "lat": 51.5, "lon": -0.1, "date": "2023-08-01"}]
    assert weather.weather_by_match(rows) == {}


def test_weather_maps_dates_via_stub(monkeypatch):
    monkeypatch.setattr(config, "WEATHER_ENABLED", True)
    monkeypatch.setattr(config, "WEATHER_MIN_INTERVAL_S", 0.0)

    def fake_fetch(lat, lon, start, end):
        table = {"2023-08-01": {"temp_c": 20.0, "precip_mm": 1.0, "wind_kmh": 10.0},
                 "2023-08-08": {"temp_c": 18.0, "precip_mm": 0.0, "wind_kmh": 25.0}}
        return table, False

    monkeypatch.setattr(weather, "_fetch_venue", fake_fetch)
    rows = [
        {"match_id": "m1", "lat": 51.5, "lon": -0.1, "date": "2023-08-01"},
        {"match_id": "m2", "lat": 51.5, "lon": -0.1, "date": "2023-08-08"},
        {"match_id": "m3", "lat": 51.5, "lon": -0.1, "date": "2099-01-01"},  # absent
    ]
    out = weather.weather_by_match(rows)
    assert out["m1"]["temp_c"] == 20.0
    assert out["m2"]["wind_kmh"] == 25.0
    assert "m3" not in out   # date hors table -> pas de clé (NaN côté features)


# --- intégration features (qualité de tir + congestion + déplacement) -------
def _make_db(matches):
    """matches : (date, home, away, hg, ag, hxg, axg, hsh, ash, hsot, asot)."""
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
               home_xg,away_xg,odds_home,odds_draw,odds_away)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""")
    for date, h, a, hg, ag, hxg, axg, hsh, ash, hsot, asot in matches:
        mid = db.make_match_id("club", date, h, a)
        conn.execute(sql, (mid, date, "2023/24", "Premier League", "club", h, a,
                           hg, ag, 0, hsh, ash, hsot, asot, hxg, axg, 2.0, 3.0, 4.0))
    conn.commit()
    return conn


# A joue tous les 7 jours ; tirs/xG connus -> proxys calculables.
_SCHED = [
    ("2023-08-01", "A", "B", 2, 0, 1.6, 0.4, 16, 8, 6, 2),
    ("2023-08-08", "A", "C", 1, 1, 1.0, 1.1, 10, 9, 4, 3),
    ("2023-08-15", "A", "B", 3, 1, 2.0, 0.7, 20, 5, 8, 1),
]


def test_shot_quality_uses_only_past(monkeypatch):
    monkeypatch.setattr(context, "coords_by_team_id", lambda conn, dom: {})
    feats = features.build_features(_make_db(_SCHED), "club").sort_values("date")
    row = feats[feats["date"] == pd.Timestamp("2023-08-15")].iloc[0]
    # xG/tir d'A sur ses 2 matchs précédents : 1.6/16=0.10 et 1.0/10=0.10 -> 0.10
    assert row["home_xg_per_shot"] == pytest.approx(0.10, abs=1e-9)
    # finition (buts - xG) : (2-1.6) et (1-1.0) -> moyenne 0.2
    assert row["home_finishing"] == pytest.approx(0.2, abs=1e-9)
    # première apparition -> pas d'historique de tir
    first = feats.iloc[0]
    assert np.isnan(first["home_xg_per_shot"])


# B joue un match À DOMICILE (Barcelone) avant de se déplacer chez A (Madrid).
_TRAVEL_SCHED = [
    ("2023-08-01", "A", "B", 2, 0, 1.6, 0.4, 16, 8, 6, 2),   # à Madrid
    ("2023-08-10", "B", "C", 1, 1, 1.0, 1.1, 10, 9, 4, 3),   # B reçoit à Barcelone
    ("2023-08-15", "A", "B", 3, 1, 2.0, 0.7, 20, 5, 8, 1),   # B -> Madrid
]


def test_congestion_and_travel_in_build(monkeypatch):
    coords = {"A": (40.45, -3.69), "B": (41.38, 2.12)}   # Madrid, Barcelone
    monkeypatch.setattr(context, "coords_by_team_id", lambda conn, dom: coords)
    feats = features.build_features(_make_db(_TRAVEL_SCHED), "club").sort_values("date")

    row = feats[feats["date"] == pd.Timestamp("2023-08-15")].iloc[0]
    # A a joué 01/08 (et n'est pas impliqué le 10/08) -> 1 match dans les 14 j.
    assert row["home_matches_14d"] == 1.0
    # A reçoit chez lui (Madrid) ; son match précédent (01/08) était à Madrid -> 0.
    assert row["home_travel_km"] == pytest.approx(0.0)
    # B vient de Barcelone (son match du 10/08 à domicile) vers Madrid -> ~505 km.
    assert 480 < row["away_travel_km"] < 530

    # Premier match : pas de lieu précédent -> déplacement NaN, congestion 0.
    first = feats.iloc[0]
    assert np.isnan(first["home_travel_km"])
    assert first["home_matches_14d"] == 0.0


# --- gating des features P6 -------------------------------------------------
def test_p6_feature_cols_gated_off(monkeypatch):
    monkeypatch.setattr(config, "P6_FEATURES_ENABLED", False)
    cols = xgb_model.active_feature_cols()
    for c in xgb_model.P6_FEATURE_COLS:
        assert c not in cols


def test_p6_feature_cols_gated_on(monkeypatch):
    monkeypatch.setattr(config, "P6_FEATURES_ENABLED", True)
    cols = xgb_model.active_feature_cols()
    for c in xgb_model.P6_FEATURE_COLS:
        assert c in cols
    assert "elo_diff" in cols   # les features historiques restent présentes
