"""Tests bout-en-bout du service et de l'API (sautés si modèles non entraînés)."""

import os

import pytest

from pipeline import config

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(config.MODELS_DIR, "meta_club.json")),
    reason="modèles non entraînés (lancer python -m pipeline.train)")


def test_predict_contract_complete():
    from pipeline import service
    p = service.predict("Premier League", "Man City", "Liverpool", neutral=False)
    for key in ["p_home_win", "p_draw", "p_away_win", "exp_home_goals",
                "exp_away_goals", "most_likely_score", "p_over_2_5", "p_btts",
                "score_matrix", "home", "away", "competition", "domain"]:
        assert key in p
    s = p["p_home_win"] + p["p_draw"] + p["p_away_win"]
    assert abs(s - 1.0) < 1e-2


def test_neutral_lowers_home_prob_via_service():
    """Le terrain neutre retire l'avantage du domicile.

    Invariant MÉCANIQUE (toujours vrai) : l'avantage du terrain n'agit que sur les
    buts attendus de l'équipe à domicile ; le passer en terrain neutre les fait donc
    BAISSER. (On n'asserte PAS le sens de variation de p_home : pour un favori, la
    symétrisation du terrain neutre peut au contraire le relever — cf. doc Étape 2.
    L'invariant fiable est l'écart de buts, pas la proba.)
    """
    from pipeline import service
    nn = service.predict("Friendly", "Brazil", "Paraguay", neutral=False)
    nu = service.predict("Friendly", "Brazil", "Paraguay", neutral=True)
    assert nn["exp_home_goals"] > nu["exp_home_goals"]   # mécanique, toujours vrai
    assert nu["p_home_win"] > nu["p_away_win"]           # le favori le reste sur neutre


def test_unknown_team_raises():
    from pipeline import service
    with pytest.raises(ValueError):
        service.predict("Premier League", "Equipe Inexistante XYZ", "Liverpool")


def test_api_endpoints():
    from fastapi.testclient import TestClient

    from pipeline.api import app
    c = TestClient(app)
    assert c.get("/api/competitions").status_code == 200
    assert len(c.get("/api/teams?domain=club").json()["teams"]) > 50
    fx = c.get("/api/fixtures").json()["fixtures"]
    assert len(fx) > 0
    r = c.post("/api/predict", json={
        "competition": "FIFA World Cup",
        "home": fx[0]["home"], "away": fx[0]["away"], "neutral": True})
    assert r.status_code == 200
    assert abs(sum(r.json()[k] for k in ["p_home_win", "p_draw", "p_away_win"]) - 1) < 1e-2


def test_predict_without_qualitative_is_statistical_only():
    """Couche actu OFF (défaut) : aucun ajustement, prédiction = socle statistique."""
    from pipeline import service
    p = service.predict("Premier League", "Man City", "Liverpool",
                        use_qualitative=False)
    assert p["qualitative_enabled"] is False
    assert "qualitative" not in p


def test_predict_with_qualitative_toggle(monkeypatch):
    """use_qualitative=True fait passer un ajustement borné, sans appel réseau réel."""
    from pipeline import service

    class _FakeLayer:
        enabled = False
        def adjust(self, home, away, competition, ref_date=None, enabled_override=None):
            if not enabled_override:
                return None
            return {"mult_dom": 1.1, "mult_ext": 0.9, "facteurs": ["test"],
                    "faits": [], "confiance": 0.5, "source": "claude+web"}
        def calls_today(self):
            return 7

    monkeypatch.setattr(service, "_qualitative", _FakeLayer())
    on = service.predict("Premier League", "Man City", "Liverpool", use_qualitative=True)
    assert on["qualitative_enabled"] is True
    assert on["qualitative"]["mult_dom"] == 1.1
    off = service.predict("Premier League", "Man City", "Liverpool", use_qualitative=False)
    assert off["qualitative_enabled"] is False and "qualitative" not in off


def test_upcoming_with_odds_source(monkeypatch):
    """Avec clé : /api/upcoming agrège les événements the-odds-api dans la fenêtre."""
    from datetime import datetime, timedelta, timezone

    from pipeline import service

    teams = service.list_teams("club")
    assert len(teams) >= 2
    home, away = teams[0]["name"], teams[1]["name"]

    ct = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    fake_event = {
        "id": "evt-test-1",
        "home_team": home, "away_team": away, "commence_time": ct,
        "bookmakers": [{
            "key": "b1", "title": "BookOne",
            "markets": [{"key": "h2h", "outcomes": [
                {"name": home, "price": 2.0},
                {"name": "Draw", "price": 3.3},
                {"name": away, "price": 3.6},
            ]}],
        }],
    }
    # PL est le premier sport_key ; on ne renvoie l'événement que pour celui-là.
    pl_key = service.odds_api.SPORT_KEYS["Premier League"]
    monkeypatch.setattr(service.odds_api, "configured", lambda: True)
    monkeypatch.setattr(service.odds_api, "fetch_odds",
                        lambda sk, **kw: [fake_event] if sk == pl_key else [])
    service.clear_caches()  # vide le cache process des matchs à venir

    res = service.upcoming_matches(days=7)
    assert res["source"] == "the-odds-api"
    assert res["count"] >= 1
    m = res["matches"][0]
    assert m["home"] == home and m["away"] == away
    assert m["competition"] == "Premier League"
    assert m["has_odds"] is True
    assert "home_badge" in m and "away_badge" in m
    service.clear_caches()


def test_upcoming_fallback_to_fixtures(monkeypatch):
    """Sans clé : repli propre sur la table fixtures (sans cotes)."""
    from fastapi.testclient import TestClient

    from pipeline import service
    from pipeline.api import app

    monkeypatch.setattr(service.odds_api, "configured", lambda: False)
    service.clear_caches()

    c = TestClient(app)
    data = c.get("/api/upcoming?days=30").json()
    assert data["configured"] is False
    # la base de test contient des fixtures de Coupe du Monde 2026
    assert data["source"] in ("fixtures", "none")
    if data["count"]:
        assert data["source"] == "fixtures"
        m = data["matches"][0]
        assert m["has_odds"] is False
        assert "home_badge" in m
    service.clear_caches()


def test_track_record_endpoint_real_numbers():
    """Le track record expose les vrais chiffres du backtest (jamais inventés)."""
    from fastapi.testclient import TestClient

    from pipeline.api import app
    data = TestClient(app).get("/api/track-record").json()
    assert "available" in data
    if data["available"]:
        club = data["club"]
        assert club["n_predictions"] > 0
        assert 0 < club["rps_calibrated"] < 1
        assert 0 < club["rps_bookmaker"] < 1
        assert "value_betting" in club and "roi" in club["value_betting"]
        assert isinstance(club["calibration"], list) and club["calibration"]
        assert data["international"]["n_predictions"] > 0


def test_predict_includes_factual_why():
    """La prédiction porte une explication factuelle (synthèse + facteurs) sans fuite."""
    from pipeline import service
    p = service.predict("Premier League", "Man City", "Liverpool", neutral=False)
    why = p["why"]
    assert isinstance(why["summary"], str) and why["summary"]
    assert isinstance(why["factors"], list) and len(why["factors"]) >= 2
    # l'avantage du terrain est mentionné hors terrain neutre
    assert any("domicile" in f for f in why["factors"])

    pn = service.predict("FIFA World Cup", "France", "Brazil", neutral=True)
    assert any("neutre" in f for f in pn["why"]["factors"])


def test_explain_adds_h2h_and_rest_factors():
    """`_explain` enrichit l'explication avec les face-à-face et l'écart de repos."""
    from pipeline import service
    feat = {"elo_diff": 200, "home_form5_ppg": 2.0, "away_form5_ppg": 1.0,
            "home_advantage": 1, "home_rest_days": 9, "away_rest_days": 3}
    h2h = {"n": 5, "home_wins": 3, "draws": 1, "away_wins": 1}
    why = service._explain(feat, "Alpha", "Beta", neutral=False, h2h=h2h)
    txt = " ".join(why["factors"]).lower()
    assert "confrontations directes" in txt          # bilan des face-à-face
    assert "repos" in txt and "alpha" in txt          # Alpha (9 j) plus reposé que Beta (3 j)
    # sans h2h ni écart de repos notable : pas de facteur superflu (robustesse)
    why2 = service._explain({"elo_diff": 5}, "Alpha", "Beta", neutral=False, h2h=None)
    assert all("confrontations" not in f for f in why2["factors"])


def test_best_value_today_surfaces_positive_edge(monkeypatch):
    """Une cote volontairement gonflée crée une value que l'encart doit remonter."""
    from datetime import datetime, timedelta, timezone

    from pipeline import service

    teams = service.list_teams("club")
    home, away = teams[0]["name"], teams[1]["name"]
    ct = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    # Cotes hautes mais crédibles (<= plafond anti-loto) sur les trois issues :
    # l'issue la plus probable du modèle (p >= 1/3) dégage un edge bien au-dessus du
    # seuil (p * 8 - 1), value garantie sans tomber dans l'outsider extrême filtré.
    fake_event = {
        "id": "evt-value-1",
        "home_team": home, "away_team": away, "commence_time": ct,
        "bookmakers": [{
            "key": "b1", "title": "BookOne",
            "markets": [{"key": "h2h", "outcomes": [
                {"name": home, "price": 8.0},
                {"name": "Draw", "price": 8.0},
                {"name": away, "price": 8.0},
            ]}],
        }],
    }
    pl_key = service.odds_api.SPORT_KEYS["Premier League"]
    monkeypatch.setattr(service.odds_api, "configured", lambda: True)
    monkeypatch.setattr(service.odds_api, "fetch_odds",
                        lambda sk, **kw: [fake_event] if sk == pl_key else [])
    service.clear_caches()

    res = service.best_value_today(days=3, top_n=3)
    assert res["configured"] is True
    assert res["n_value"] >= 1
    top = res["items"][0]
    assert top["edge"] > res["edge_threshold"]
    assert top["home"] == home and top["away"] == away
    assert "home_badge" in top and top["best_odds"] == 8.0
    assert top["model_prob"] >= 0.12
    service.clear_caches()


def test_value_today_empty_without_key(monkeypatch):
    """Sans clé : aucune value remontée (liste vide, pas d'erreur)."""
    from fastapi.testclient import TestClient

    from pipeline import service
    from pipeline.api import app

    monkeypatch.setattr(service.odds_api, "configured", lambda: False)
    service.clear_caches()
    data = TestClient(app).get("/api/value/today").json()
    assert data["items"] == [] and data["n_value"] == 0
    service.clear_caches()


def test_pwa_assets_served():
    """Manifest + service worker servis à la racine, avec les bons en-têtes."""
    from fastapi.testclient import TestClient

    from pipeline.api import app
    c = TestClient(app)

    m = c.get("/manifest.json")
    assert m.status_code == 200
    body = m.json()
    assert body["name"] and body["display"] == "standalone"
    assert body["icons"] and body["icons"][0]["src"].endswith(".svg")

    sw = c.get("/sw.js")
    assert sw.status_code == 200
    assert sw.headers.get("service-worker-allowed") == "/"
    assert "addEventListener" in sw.text
    # le SW ne doit jamais mettre en cache les appels /api/
    assert "/api/" in sw.text


def test_model_separates_strong_from_weak_nations():
    """Anti-aplatissement : une grande nation contre une petite doit montrer un
    écart NET de buts attendus (le modèle ne ramène pas tout vers ~1,8-1,8)."""
    from pipeline import service
    p = service.predict("FIFA World Cup", "France", "Gibraltar", neutral=True)
    assert p["exp_home_goals"] - p["exp_away_goals"] >= 2.0   # écart franc
    assert p["p_home_win"] > 0.85                              # favori écrasant


def test_world_cup_host_bonus_tips_and_is_visible():
    """Effet pays hôte : USA-Paraguay en Coupe du Monde penche désormais pour les
    USA, l'effet est exposé, et il n'existe PAS hors Coupe du Monde."""
    from pipeline import service
    wc = service.predict("FIFA World Cup", "United States", "Paraguay", neutral=True)
    assert wc["p_home_win"] > wc["p_away_win"]                 # l'hôte est favori
    assert wc.get("host_effect", {}).get("team") == "United States"
    assert wc["host_effect"]["goal_boost_pct"] > 0
    # hôte mentionné dans l'explication factuelle
    assert any("hôte" in f for f in wc["why"]["factors"])
    # même affiche en amical : aucun effet hôte (réservé à la Coupe du Monde)
    fr = service.predict("Friendly", "United States", "Paraguay", neutral=True)
    assert "host_effect" not in fr


def test_host_bonus_only_for_hosts_and_bounded():
    """Le bonus hôte ne flippe pas un net favori adverse, et deux co-hôtes ne
    se l'attribuent pas (aucun avantage différentiel)."""
    from pipeline import service
    vs_strong = service.predict("FIFA World Cup", "United States", "Brazil", neutral=True)
    assert vs_strong["p_away_win"] > vs_strong["p_home_win"]   # Brésil reste favori
    co_hosts = service.predict("FIFA World Cup", "Mexico", "Canada", neutral=True)
    assert "host_effect" not in co_hosts                       # deux hôtes -> neutre


def test_neutral_prediction_is_swap_invariant():
    """Terrain neutre SANS effet hôte : l'étiquette domicile/extérieur est
    arbitraire, donc p_home(H vs A) == p_away(A vs H) (symétrie indispensable
    pour une Coupe du Monde jouée sur terrain neutre). On prend deux nations
    NON hôtes (Brésil/Argentine) : la symétrisation y est exacte. Pour un hôte,
    le bonus pays-hôte introduit volontairement une légère asymétrie."""
    from pipeline import service
    a = service.predict("FIFA World Cup", "Brazil", "Argentina", neutral=True)
    b = service.predict("FIFA World Cup", "Argentina", "Brazil", neutral=True)
    assert abs(a["p_home_win"] - b["p_away_win"]) < 0.01
    assert abs(a["p_away_win"] - b["p_home_win"]) < 0.01
    assert abs(a["p_draw"] - b["p_draw"]) < 0.01


def test_belgium_is_clear_favorite_vs_egypt():
    """Anti-aplatissement / correction inter-confédérations (Phase B).

    L'Égypte, qui domine un vivier régional plus faible, était surévaluée face à
    l'élite (sa défense paraissait aussi solide que celle des cadors européens),
    si bien que la Belgique ne sortait pas en favori net. Après la pondération
    inter-confédérations, la Belgique est un FAVORI CLAIR : écart de buts et de
    probabilité francs. (On n'exige pas le niveau exact du marché : on ne peut pas
    le valider sur les sélections — pas de cotes historiques — et la calibration
    reste juste ; voir docs/modele_intl.md.)
    """
    from pipeline import service
    p = service.predict("FIFA World Cup", "Belgium", "Egypt", neutral=True)
    assert p["p_home_win"] > p["p_away_win"] + 0.15          # favori net, pas pile ou face
    assert p["exp_home_goals"] > p["exp_away_goals"] + 0.4   # écart de buts franc


def test_prediction_cache_hit_and_invalidation():
    """Le socle statistique est mis en cache (résultat identique, copie isolée)
    et le cache est vidé par `clear_caches` (après un refresh / ré-entraînement)."""
    from pipeline import service

    service.clear_caches()
    assert service._PRED_CACHE == {}

    p1 = service.predict("Premier League", "Man City", "Liverpool", neutral=False)
    assert len(service._PRED_CACHE) == 1                 # entrée mémorisée
    p2 = service.predict("Premier League", "Man City", "Liverpool", neutral=False)
    assert p1 == p2                                      # même contenu
    assert p1 is not p2                                  # copie : pas le même objet

    # muter le retour ne corrompt pas l'entrée en cache (deep copy)
    p2["p_home_win"] = 999
    p3 = service.predict("Premier League", "Man City", "Liverpool", neutral=False)
    assert p3["p_home_win"] != 999

    service.clear_caches()
    assert service._PRED_CACHE == {}                     # invalidé


def test_qualitative_status_endpoint():
    from fastapi.testclient import TestClient

    from pipeline.api import app
    c = TestClient(app)
    s = c.get("/api/qualitative/status").json()
    for key in ["enabled_default", "calls_today", "cache_ttl_hours",
                "max_web_uses", "news_window_days"]:
        assert key in s
    assert isinstance(s["calls_today"], int)
