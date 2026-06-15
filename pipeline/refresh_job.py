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

from . import config, db, features, refresh, service, train

log = logging.getLogger("pipeline.refresh_job")

_lock = threading.Lock()
_state: dict = {
    "state": "idle",          # idle | running | done | error
    "mode": None,             # rapide | complet
    "started_at": None,
    "finished_at": None,
    "message": "",
}
# Génération du job : un worker abandonné (timeout) ne doit jamais réécrire l'état
# d'un job plus récent. Incrémentée à chaque `run`.
_generation = 0


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


def _do_work(mode: str, result: dict) -> None:
    """Le travail réel (réseau + features + éventuel entraînement).

    Écrit SON résultat dans `result` (jamais dans `_state`) : si ce worker est
    abandonné après un dépassement de délai, il ne peut pas corrompre l'état d'un
    job plus récent. Le contrôleur (`run`) seul met `_state` à jour.
    """
    try:
        quick = mode != "complet"
        summary = refresh.refresh(use_cache=False, quick=quick)
        feats = features.build_all()
        msg = (f"Données rafraîchies : {summary.get('clubs', 0)} matchs clubs, "
               f"{summary.get('internationals', 0)} sélections, "
               f"{summary.get('fixtures', 0)} matchs à venir ; "
               f"features clubs={feats.get('club', 0)}, intl={feats.get('international', 0)}.")
        if mode == "complet":
            train.train_all()
            msg += " Modèles réentraînés."
        service.clear_caches()
        result["state"] = "done"
        result["message"] = msg
    except Exception as e:  # noqa: BLE001 — jamais de crash, on remonte l'erreur
        log.exception("Mise à jour %s en échec", mode)
        result["state"] = "error"
        result["message"] = f"Échec de la mise à jour : {e}"


def run(mode: str = "rapide") -> None:
    """Pilote la mise à jour avec un TIMEOUT GLOBAL DUR : le job ne peut jamais
    rester bloqué. Le travail tourne dans un thread ; au-delà du délai, l'état
    bascule proprement en « error » et le thread (démon) est abandonné.
    """
    global _generation
    with _lock:
        _generation += 1
        gen = _generation
    timeout = (config.REFRESH_TIMEOUT_FULL_S if mode == "complet"
               else config.REFRESH_TIMEOUT_QUICK_S)

    result: dict = {}
    worker = threading.Thread(target=_do_work, args=(mode, result), daemon=True)
    worker.start()
    worker.join(timeout)

    with _lock:
        if gen != _generation:           # un job plus récent a pris la main
            return
        if worker.is_alive():            # dépassement de délai : jamais bloqué
            _state.update(state="error", finished_at=_now(),
                          message=("Délai dépassé : le réseau est trop lent. "
                                   "Réessaie plus tard (les sources lentes sont mises en cache)."))
            log.error("Mise à jour %s : délai global de %.0fs dépassé", mode, timeout)
            return
        if result.get("state") == "done":
            _persist_last_updated()
            _state.update(state="done", finished_at=_now(), message=result["message"])
            log.info("Mise à jour %s terminée : %s", mode, result["message"])
        else:
            _state.update(state="error", finished_at=_now(),
                          message=result.get("message", "Échec de la mise à jour."))


def cancel() -> bool:
    """Annule la mise à jour en cours, immédiatement et proprement.

    On incrémente la génération : le worker (thread démon) en cours devient
    « périmé » et `run` ignorera son résultat ; on libère le verrou en repassant à
    l'état `idle`. Le thread se terminera seul en arrière-plan (ou expirera), sans
    jamais réécrire l'état. Renvoie False si aucune mise à jour n'est en cours.
    """
    global _generation
    with _lock:
        if _state["state"] != "running":
            return False
        _generation += 1        # invalide le worker courant (run() bailera)
        _state.update(state="idle", mode=None, finished_at=_now(),
                      message="Mise à jour annulée.")
    log.info("Mise à jour annulée par l'utilisateur")
    return True


def status() -> dict:
    s = dict(_state)
    s["last_updated"] = last_updated()
    s["running"] = is_running()
    return s
