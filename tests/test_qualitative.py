"""Tests de la couche qualitative (Phases 5 + 7).

Critères : prédiction identique couche OFF ; couche ON -> ajustement borné (±25 %),
justifié et SOURCÉ (faits datés < 7 j avec URL) ; replis propres (web non relayé,
réseau KO) sans jamais crasher ; cache avec TTL. Aucun appel réseau réel : on injecte
un faux client Anthropic et on mocke la recherche web.
"""

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline import config
from pipeline.dixon_coles import DixonColesModel
from pipeline.ensemble import EnsemblePredictor
from pipeline.qualitative import QualitativeLayer, _clamp


def _toy_predictor():
    dc = DixonColesModel(teams=["A", "B"], attack={"A": 0.3, "B": -0.1},
                         defence={"A": -0.1, "B": 0.2}, base=0.2, home_adv=0.25,
                         rho=-0.05, converged=True)
    return EnsemblePredictor(dc, None, None, config.DOMAIN_INTL)


class _Block:
    def __init__(self, text="", type="text"):
        self.text, self.type = text, type


class _FakeMsg:
    def __init__(self, blocks):
        self.content = blocks


class _FakeClient:
    """Simule l'API : extraction AVEC web_search (renvoie des faits datés + URL),
    puis synthèse (JSON de multiplicateurs). `fact_date` pilote la fraîcheur."""

    def __init__(self, mult_dom=1.5, mult_ext=0.9, fact_date=None, relay_web=True):
        self.mult_dom, self.mult_ext = mult_dom, mult_ext
        self.fact_date = fact_date or date.today().isoformat()
        self.relay_web = relay_web
        self.web_tool_seen = False
        self.messages = self

    def create(self, model, max_tokens, messages, tools=None):
        if model == config.QUALITATIVE_MODEL_EXTRACTION:
            assert tools is not None and tools[0]["name"] == "web_search"
            self.web_tool_seen = True
            facts = (f'[{{"fait": "Buteur clé de B forfait (blessure ischio)", '
                     f'"equipe": "B", "date": "{self.fact_date}", '
                     f'"source_titre": "L Equipe", '
                     f'"source_url": "https://lequipe.fr/article"}}]')
            blocks = [_Block(text=facts)]
            if self.relay_web:        # le proxy a bien relayé l'outil web_search
                blocks.insert(0, _Block(type="server_tool_use"))
            return _FakeMsg(blocks)
        return _FakeMsg([_Block(
            text=f'{{"mult_dom": {self.mult_dom}, "mult_ext": {self.mult_ext}, '
                 f'"facteurs": ["Buteur de B absent"], "confiance": 0.6}}')])


def _mem_conn():
    return lambda *a: sqlite3.connect(":memory:")


# --- garde-fous historiques (Phase 5) --------------------------------------
def test_clamp_bounds():
    assert _clamp(99) == 1.0 + config.QUALITATIVE_MAX_ADJ
    assert _clamp(-99) == 1.0 - config.QUALITATIVE_MAX_ADJ
    assert _clamp(1.1) == 1.1
    assert _clamp("nan") == 1.0


def test_layer_off_returns_none():
    layer = QualitativeLayer(enabled=False)
    assert layer.adjust("A", "B", "FIFA World Cup") is None


def test_prediction_identical_when_off():
    pred = _toy_predictor()
    base = pred.predict("A", "B", neutral=True)
    with_none = pred.predict("A", "B", neutral=True, adjustment=None)
    assert base["p_home_win"] == with_none["p_home_win"]
    assert "qualitative" not in base


def test_layer_on_is_bounded(tmp_path):
    db_file = tmp_path / "cache.db"
    layer = QualitativeLayer(enabled=True,
                             client=_FakeClient(mult_dom=99, mult_ext=0.01),
                             conn_factory=lambda *a: sqlite3.connect(db_file))
    adj = layer.adjust("A", "B", "FIFA World Cup")
    assert adj["mult_dom"] == pytest.approx(1.25)
    assert adj["mult_ext"] == pytest.approx(0.75)
    assert 0.0 <= adj["confiance"] <= 1.0


def test_failure_falls_back_to_none():
    class _Boom:
        messages = property(lambda self: (_ for _ in ()).throw(RuntimeError("net")))
    layer = QualitativeLayer(enabled=True, client=_Boom(), conn_factory=_mem_conn())
    assert layer.adjust("A", "B", "Friendly") is None


# --- Phase 7 : conscience de l'actualité -----------------------------------
def test_facts_are_recent_sourced_and_used(tmp_path):
    """Acceptation principale : la blessure d'un cadre de B abaisse (dans la borne)
    ses buts attendus, et le fait + sa source datée < 7 j sont visibles."""
    db_file = tmp_path / "cache.db"
    fake = _FakeClient(mult_dom=1.0, mult_ext=0.8)
    layer = QualitativeLayer(enabled=True, client=fake,
                             conn_factory=lambda *a: sqlite3.connect(db_file))
    adj = layer.adjust("A", "B", "FIFA World Cup")

    assert fake.web_tool_seen                      # l'outil web a bien été demandé
    assert adj["mult_ext"] == pytest.approx(0.8)
    assert adj["faits"], "les faits sourcés doivent être renvoyés"
    f = adj["faits"][0]
    assert f["source_url"].startswith("http")
    within = date.today() - date.fromisoformat(f["date"]) <= timedelta(
        days=config.QUALITATIVE_NEWS_WINDOW_DAYS)
    assert within
    assert adj["source"] == "claude+web"

    # effet borné : B (extérieur) voit ses buts attendus baisser
    pred = _toy_predictor()
    base = pred.predict("A", "B", neutral=True)
    boosted = pred.predict("A", "B", neutral=True, adjustment=adj)
    assert boosted["exp_away_goals"] < base["exp_away_goals"]
    assert boosted["p_home_win"] > base["p_home_win"]


def test_old_facts_are_filtered_out(tmp_path):
    """Un fait daté hors fenêtre (30 j) est ignoré -> aucun ajustement."""
    db_file = tmp_path / "cache.db"
    old = (date.today() - timedelta(days=30)).isoformat()
    layer = QualitativeLayer(enabled=True,
                             client=_FakeClient(fact_date=old),
                             conn_factory=lambda *a: sqlite3.connect(db_file))
    assert layer.adjust("A", "B", "FIFA World Cup") is None


def test_web_not_relayed_and_no_provider_returns_none(tmp_path):
    """Proxy ne relaie pas web_search et aucun fournisseur configuré -> None propre."""
    db_file = tmp_path / "cache.db"
    layer = QualitativeLayer(enabled=True,
                             client=_FakeClient(relay_web=False),
                             conn_factory=lambda *a: sqlite3.connect(db_file))
    assert layer.adjust("A", "B", "FIFA World Cup") is None


def test_fresh_cache_served_without_network(tmp_path):
    """Une entrée de cache fraîche est servie sans rappeler le client."""
    db_file = tmp_path / "cache.db"
    factory = lambda *a: sqlite3.connect(db_file)
    layer = QualitativeLayer(enabled=True, client=_FakeClient(mult_ext=0.8),
                             conn_factory=factory)
    first = layer.adjust("A", "B", "FIFA World Cup")
    assert first is not None

    class _Boom:
        messages = property(lambda self: (_ for _ in ()).throw(RuntimeError("net")))
    cached_layer = QualitativeLayer(enabled=True, client=_Boom(), conn_factory=factory)
    again = cached_layer.adjust("A", "B", "FIFA World Cup")   # ne doit PAS appeler le client
    assert again["mult_ext"] == pytest.approx(0.8)


# --- Étape 1 : activation par requête + compteur d'appels du jour ----------
def test_enabled_override_forces_on_and_off(tmp_path):
    """L'UI peut activer/désactiver la couche par requête, sans toucher au .env."""
    db_file = tmp_path / "cache.db"
    factory = lambda *a: sqlite3.connect(db_file)
    # couche OFF par défaut mais forcée ON via override -> ajustement produit
    layer_off = QualitativeLayer(enabled=False, client=_FakeClient(mult_ext=0.8),
                                 conn_factory=factory)
    assert layer_off.adjust("A", "B", "FIFA World Cup") is None
    forced_on = layer_off.adjust("A", "B", "FIFA World Cup", enabled_override=True)
    assert forced_on is not None and forced_on["mult_ext"] == pytest.approx(0.8)

    # couche ON par défaut mais forcée OFF via override -> None
    layer_on = QualitativeLayer(enabled=True, client=_FakeClient(),
                                conn_factory=factory)
    assert layer_on.adjust("C", "D", "FIFA World Cup", enabled_override=False) is None


def test_call_counter_increments_on_real_calls(tmp_path):
    """Le compteur du jour augmente quand de vrais appels LLM partent (cache-miss)."""
    db_file = tmp_path / "cache.db"
    factory = lambda *a: sqlite3.connect(db_file)
    layer = QualitativeLayer(enabled=True, client=_FakeClient(mult_ext=0.8),
                             conn_factory=factory)
    assert layer.calls_today() == 0
    layer.adjust("A", "B", "FIFA World Cup")          # extraction + synthèse
    assert layer.calls_today() == 2
    # second appel identique : servi par le cache frais -> aucun appel supplémentaire
    layer.adjust("A", "B", "FIFA World Cup")
    assert layer.calls_today() == 2


def test_stale_cache_refreshes(tmp_path):
    """Au-delà du TTL, on réinterroge (et on tolère le repli sur le périmé si KO)."""
    db_file = tmp_path / "cache.db"
    factory = lambda *a: sqlite3.connect(db_file)
    layer = QualitativeLayer(enabled=True, client=_FakeClient(mult_ext=0.8),
                             conn_factory=factory)
    key = layer._cache_key("A", "B", "FIFA World Cup", date.today())
    layer._cache_put(key, {"mult_dom": 1.0, "mult_ext": 0.9, "facteurs": [],
                           "faits": [], "confiance": 0.1, "source": "claude+web"})
    # rendre l'entrée périmée (au-delà du TTL)
    conn = sqlite3.connect(db_file)
    old = (datetime.now(timezone.utc) - timedelta(
        hours=config.QUALITATIVE_CACHE_TTL_HOURS + 1)).isoformat(timespec="seconds")
    conn.execute("UPDATE news_cache SET created_at = ?", (old,))
    conn.commit()
    conn.close()

    fresh = layer.adjust("A", "B", "FIFA World Cup")   # doit réinterroger le client
    assert fresh["mult_ext"] == pytest.approx(0.8)
    assert fresh["faits"]
