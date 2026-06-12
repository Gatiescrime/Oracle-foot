"""Tests Phase P5 : valeur d'effectif + Elo offensif/défensif + avantage terrain.

Tout est hors ligne et déterministe. On vérifie :
  - l'appariement des noms de la valeur d'effectif (exact + flou + manquant -> NaN) ;
  - l'avantage du terrain par compétition, nul en terrain neutre ;
  - que l'Elo off/déf sépare bien attaque et défense, et reste sans fuite ;
  - le branchement (gating) des features P5 derrière le drapeau de config.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline import config, db, elo, squad_value, xgb_model


# --- valeur d'effectif : parsing + alignement -------------------------------
def test_read_csv_ignores_comments_and_blanks():
    text = (
        "# commentaire\n"
        "team,value_meur\n"
        "Arsenal,1250\n"
        "\n"
        "# autre commentaire\n"
        "Real Madrid,1300\n"
        "Bad,notanumber\n"
        "Zero,0\n"
    )
    vals = squad_value._read_csv_text(text)
    assert vals == {"Arsenal": 1250.0, "Real Madrid": 1300.0}


def test_align_exact_and_fuzzy():
    teams = {"c_arsenal": "Arsenal", "c_real_madrid": "Real Madrid"}
    raw = {"Arsenal": 1250.0, "Real Madrid CF": 1300.0}  # 2e = variante proche
    aligned = squad_value._align_to_teams(raw, teams)
    assert aligned["c_arsenal"] == 1250.0
    # "Real Madrid CF" rejoint "Real Madrid" par appariement flou (préfixe)
    assert aligned["c_real_madrid"] == 1300.0


def test_align_unknown_team_dropped():
    teams = {"c_arsenal": "Arsenal"}
    raw = {"Tottenham Hotspur FC XYZ": 800.0}  # aucun canonique proche
    aligned = squad_value._align_to_teams(raw, teams)
    assert "c_arsenal" not in aligned  # rien d'assez proche -> non apparié


def test_values_by_team_id_empty_without_file(monkeypatch):
    monkeypatch.setattr(config, "SQUAD_VALUE_URL", "")
    monkeypatch.setattr(config, "SQUAD_VALUE_CSV", "/chemin/inexistant.csv")
    conn = db.connect(":memory:")
    db.reset_schema(conn)
    assert squad_value.values_by_team_id(conn, "club") == {}


# --- avantage du terrain par compétition ------------------------------------
def test_home_advantage_zero_on_neutral():
    assert elo.home_advantage_for("FIFA World Cup", is_club=False, neutral=True) == 0.0


def test_home_advantage_varies_by_competition():
    epl = elo.home_advantage_for("Premier League", is_club=True)
    liga = elo.home_advantage_for("La Liga", is_club=True)
    quals = elo.home_advantage_for("FIFA World Cup qualification", is_club=False)
    assert liga > epl                  # La Liga > Premier League (valeurs fixées)
    assert quals > liga                # qualifs : très fort avantage du terrain
    # compétition inconnue -> valeur par défaut, finie et positive
    assert elo.home_advantage_for("Compétition X", is_club=True) > 0


# --- Elo offensif / défensif ------------------------------------------------
def _toy_matches():
    """A = grosse attaque/grosse fuite défensive ; B = solide derrière, peu d'attaque."""
    rows = []
    for i in range(20):
        d = pd.Timestamp("2022-01-01") + pd.Timedelta(days=7 * i)
        # A marque beaucoup et encaisse pas mal ; C est une équipe quelconque
        rows.append({"date": d, "competition": "Premier League",
                     "home_team": "A", "away_team": "C",
                     "home_goals": 4, "away_goals": 2, "neutral": False})
    for i in range(20):
        d = pd.Timestamp("2022-06-01") + pd.Timedelta(days=7 * i)
        # B gagne 1-0 : faible attaque, défense de fer
        rows.append({"date": d, "competition": "Premier League",
                     "home_team": "B", "away_team": "C",
                     "home_goals": 1, "away_goals": 0, "neutral": False})
    return pd.DataFrame(rows)


def test_offdef_separates_attack_and_defense():
    df = elo.compute_elo_offdef(_toy_matches(), is_club=True)
    final = df.attrs["final_offdef"]
    # A : attaque très au-dessus de la moyenne, défense en dessous
    assert final["A"]["off"] > elo.INITIAL_RATING
    assert final["A"]["def"] < elo.INITIAL_RATING
    # B : défense très au-dessus, attaque proche/en dessous de la moyenne
    assert final["B"]["def"] > elo.INITIAL_RATING
    assert final["B"]["off"] < final["A"]["off"]


def test_offdef_is_prematch_no_leak():
    df = _toy_matches()
    full = elo.compute_elo_offdef(df, is_club=True)
    # tronque après le 10e match : la ligne du 10e ne doit pas bouger
    trunc = elo.compute_elo_offdef(df.iloc[:10].copy(), is_club=True)
    cols = ["home_off_elo", "home_def_elo", "away_off_elo", "away_def_elo"]
    pd.testing.assert_frame_equal(
        full.iloc[:10][cols].reset_index(drop=True),
        trunc[cols].reset_index(drop=True), check_dtype=False)


def test_offdef_first_match_starts_at_initial():
    df = elo.compute_elo_offdef(_toy_matches(), is_club=True)
    first = df.iloc[0]
    assert first["home_off_elo"] == pytest.approx(elo.INITIAL_RATING)
    assert first["away_def_elo"] == pytest.approx(elo.INITIAL_RATING)


# --- gating des features P5 -------------------------------------------------
def test_p5_feature_cols_gated_off(monkeypatch):
    monkeypatch.setattr(config, "P5_FEATURES_ENABLED", False)
    monkeypatch.setattr(config, "MARKET_FEATURES_ENABLED", False)
    cols = xgb_model.active_feature_cols()
    assert "home_off_elo" not in cols
    assert "squad_value_logratio" not in cols


def test_p5_feature_cols_gated_on(monkeypatch):
    monkeypatch.setattr(config, "P5_FEATURES_ENABLED", True)
    cols = xgb_model.active_feature_cols()
    for c in xgb_model.P5_FEATURE_COLS:
        assert c in cols
    # les features historiques restent présentes
    assert "elo_diff" in cols and "home_elo" in cols
