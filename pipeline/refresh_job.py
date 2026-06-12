"""Mise à jour des données « en temps présent », pilotée depuis l'UI.

Un clic sur « Mettre à jour les données » lance, EN TÂCHE DE FOND :
  (a) refresh.refresh(use_cache=False) : re-télécharge résultats récents + calendrier ;
  (b) features.build_all() : reconstruit les features sans fuite (Elo + forme + xG) ;
  (c) en mode « complet » : train.train_all() réentraîne et réexporte les modèles ;
  (d) service.clear_caches() : l'app sert immédiatement les données fraîches.

Deux modes :
  - « rapide »  : (a) + (b) + (d), sans réentraînement ;
  - « complet » : (a) + (b) + (c) + (d).

L'état est suivi en mémoire (un seul job à la fois, verrou anti-double-clic) et
l'horodatage de la dernière mise à jour réussie est persisté en base (table app_meta).
Aucune source réseau indisponible ne fait planter l'app : l'erreur est capturée et
remontée dans le statut.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from . import db, features, refresh, service, train

log = logging.getLogger("pipeline.refresh_job")

_lock = threading.Lock()
_state: dict = {
    "state": "idle",          # idle | running | done | error
    "mode": None,             # rapide | complet
    "started_at": None,
    "finished_at": None,
    "message": "",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _persist_last_updated() -> None:
    try:
        conn = db.connect()
        conn.execute("CREATE TABLE IF NOT EXISTS app_meta "
                     "(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO app_meta(key, value) VALUES('last_updated', ?)",
                     (_now(),))
        conn.commit()
        conn.close()
    except Exception as e:  # best-effort
        log.debug("persistance last_updated impossible : %s", e)


def last_updated() -> str | None:
    try:
        conn = db.connect()
        conn.execute("CREATE TABLE IF NOT EXISTS app_meta "
                     "(key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'last_updated'").fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def is_running() -> bool:
    return _state["state"] == "running"


def start(mode: str = "rapide") -> bool:
    """Réserve le job (atomique). False si une mise à jour est déjà en cours."""
    with _lock:
        if _state["state"] == "running":
            return False
        _state.update(state="running", mode=mode, started_at=_now(),
                      finished_at=None, message="")
    return True


def run(mode: str = "rapide") -> None:
    """Exécute la mise à jour (bloquant) ; appelé en tâche de fond après `start`."""
    try:
        summary = refresh.refresh(use_cache=False)
        feats = features.build_all()
        msg = (f"Données rafraîchies : {summary.get('clubs', 0)} matchs clubs, "
               f"{summary.get('internationals', 0)} sélections, "
               f"{summary.get('fixtures', 0)} matchs à venir ; "
               f"features clubs={feats.get('club', 0)}, intl={feats.get('international', 0)}.")
        if mode == "complet":
            train.train_all()
            msg += " Modèles réentraînés."
        service.clear_caches()
        _persist_last_updated()
        _state.update(state="done", finished_at=_now(), message=msg)
        log.info("Mise à jour %s terminée : %s", mode, msg)
    except Exception as e:  # noqa: BLE001 — jamais de crash, on remonte l'erreur
        log.exception("Mise à jour %s en échec", mode)
        _state.update(state="error", finished_at=_now(),
                      message=f"Échec de la mise à jour : {e}")


def status() -> dict:
    s = dict(_state)
    s["last_updated"] = last_updated()
    s["running"] = is_running()
    return s
