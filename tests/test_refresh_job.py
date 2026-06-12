"""Tests de la mise à jour des données en tâche de fond (Étape 2).

On ne touche JAMAIS au réseau ni à l'entraînement réel : les étapes lourdes
(refresh, features, train) sont remplacées par des doublures. On vérifie la machine
à états, le verrou anti-double-clic, la persistance de l'horodatage et le repli propre
en cas d'erreur de source.
"""

import sqlite3

import pytest

from pipeline import refresh_job


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Réinitialise l'état et isole la base (table app_meta) dans un fichier temporaire."""
    refresh_job._state.update(state="idle", mode=None, started_at=None,
                              finished_at=None, message="")
    db_file = tmp_path / "meta.db"
    monkeypatch.setattr(refresh_job.db, "connect", lambda *a, **k: sqlite3.connect(db_file))
    yield


def _stub_pipeline(monkeypatch, fail=False):
    calls = {"refresh": 0, "features": 0, "train": 0, "clear": 0}

    def fake_refresh(use_cache=True):
        calls["refresh"] += 1
        if fail:
            raise RuntimeError("source réseau indisponible")
        return {"clubs": 100, "internationals": 50, "fixtures": 12}

    monkeypatch.setattr(refresh_job.refresh, "refresh", fake_refresh)
    monkeypatch.setattr(refresh_job.features, "build_all",
                        lambda *a, **k: (calls.__setitem__("features", calls["features"] + 1)
                                         or {"club": 100, "international": 50}))
    monkeypatch.setattr(refresh_job.train, "train_all",
                        lambda *a, **k: calls.__setitem__("train", calls["train"] + 1))
    monkeypatch.setattr(refresh_job.service, "clear_caches",
                        lambda: calls.__setitem__("clear", calls["clear"] + 1))
    return calls


def test_quick_mode_runs_data_and_features_not_training(monkeypatch):
    calls = _stub_pipeline(monkeypatch)
    assert refresh_job.start("rapide") is True
    refresh_job.run("rapide")
    s = refresh_job.status()
    assert s["state"] == "done"
    assert calls == {"refresh": 1, "features": 1, "train": 0, "clear": 1}
    assert s["last_updated"] is not None


def test_full_mode_also_retrains(monkeypatch):
    calls = _stub_pipeline(monkeypatch)
    refresh_job.start("complet")
    refresh_job.run("complet")
    assert refresh_job.status()["state"] == "done"
    assert calls["train"] == 1


def test_lock_prevents_double_run(monkeypatch):
    _stub_pipeline(monkeypatch)
    assert refresh_job.start("rapide") is True       # réservé
    assert refresh_job.start("rapide") is False      # déjà en cours -> refusé


def test_network_error_is_caught(monkeypatch):
    calls = _stub_pipeline(monkeypatch, fail=True)
    refresh_job.start("rapide")
    refresh_job.run("rapide")                         # ne doit PAS lever
    s = refresh_job.status()
    assert s["state"] == "error"
    assert "indisponible" in s["message"]
    assert calls["features"] == 0                     # interrompu avant les features


def test_api_refresh_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from pipeline import api
    # la tâche de fond ne doit rien télécharger : on neutralise run()
    monkeypatch.setattr(api.refresh_job, "run", lambda mode="rapide": None)
    c = TestClient(api.app)

    r = c.post("/api/refresh?mode=rapide")
    assert r.status_code == 200 and r.json()["state"] == "running"

    st = c.get("/api/refresh/status").json()
    for key in ["state", "mode", "started_at", "finished_at", "message",
                "last_updated", "running"]:
        assert key in st


def test_api_refresh_conflict_when_running(monkeypatch):
    from fastapi.testclient import TestClient

    from pipeline import api
    refresh_job._state.update(state="running", mode="complet")   # simule un job en cours
    c = TestClient(api.app)
    r = c.post("/api/refresh?mode=rapide")
    assert r.status_code == 409
