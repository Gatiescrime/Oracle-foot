"""Mise à jour des données « en temps présent », pilotée depuis l'UI.

Un clic sur « Mettre à jour les données » lance, EN TÂCHE DE FOND, DEUX phases au
budget de temps SÉPARÉ (essentiel : le réseau ne doit pas tuer le réentraînement) :

  Phase 1 — DONNÉES (budget court, `REFRESH_DATA_TIMEOUT_S`) :
    (a) refresh.refresh(quick=True) : ne re-télécharge QUE le léger récent
        (saison clubs en cours + sélections martj42) et RÉUTILISE le cache des
        sources lentes (xG/joueurs understat). Timeouts réseau courts par source.
    (b) features.build_all() : reconstruit les features sans fuite (Elo + forme + xG).

  Phase 2 — RÉENTRAÎNEMENT (budget large, `REFRESH_TRAIN_TIMEOUT_S`, « complet » seul) :
    (c) train.train_all() : réentraîne et réexporte les modèles. Légitimement long,
        donc protégé par son PROPRE budget — jamais tué par le timeout réseau.

  Enfin : service.clear_caches() pour servir immédiatement les données fraîches.

Deux modes :
  - « rapide »  : phase 1 seulement ;
  - « complet » : phase 1 PUIS phase 2. Les DEUX modes réutilisent le cache des
    sources lentes (le complet ne re-scrape plus understat depuis zéro).

L'état est suivi en mémoire (un seul job à la fois, verrou anti-double-clic) et
l'horodatage de la dernière mise à jour réussie est persisté en base (table app_meta).
Aucune source réseau indisponible ne fait planter l'app : l'erreur est capturée et
remontée. Le bouton « Annuler » interrompt proprement à tout moment (cf. `cancel`).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from . import config, correction, db, features, journal, refresh, service, train, wc_bilan

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


def _do_data(result: dict) -> None:
    """Phase 1 : données légères récentes (cache des sources lentes) + features.

    Écrit SON résultat dans `result` (jamais dans `_state`) : un worker abandonné
    (timeout / annulation) ne peut pas corrompre l'état d'un job plus récent.
    `quick=True` même en mode complet : on ne re-scrape PAS understat depuis zéro.
    """
    try:
        summary = refresh.refresh(use_cache=False, quick=True)
        feats = features.build_all()
        # Apprentissage : régler les prédictions en attente dont le résultat est connu,
        # puis recalculer la correction bornée (validée hors échantillon) sur TOUTES les
        # erreurs accumulées. Best-effort : un souci ne doit jamais faire échouer la MAJ.
        try:
            settled = journal.settle_pending()
            correction.refit_from_journal()
        except Exception as e:  # noqa: BLE001
            log.warning("apprentissage (journal/correction) ignoré : %s", e)
            settled = 0
        # Note : la nouvelle correction prend effet à la FINALISATION du job, quand
        # `service.clear_caches()` vide le cache de prédiction en même temps que les
        # données fraîches (la mise à jour est « atomique » du point de vue de l'app :
        # l'état pré-refresh est servi tant que le job n'est pas terminé).
        result["state"] = "done"
        result["message"] = (
            f"Données rafraîchies : {summary.get('clubs', 0)} matchs clubs, "
            f"{summary.get('internationals', 0)} sélections, "
            f"{summary.get('fixtures', 0)} matchs à venir ; "
            f"features clubs={feats.get('club', 0)}, intl={feats.get('international', 0)}"
            + (f" ; {settled} prédiction(s) réglée(s)." if settled else "."))
    except Exception as e:  # noqa: BLE001 — jamais de crash, on remonte l'erreur
        log.exception("Phase données en échec")
        result["state"] = "error"
        result["message"] = f"Échec de la mise à jour : {e}"


def _do_train(result: dict) -> None:
    """Phase 2 : réentraînement des modèles (légitimement long, budget dédié)."""
    try:
        train.train_all()
        # Bilan CdM (walk-forward anti-fuite) : incrémental (seules les nouvelles dates
        # sont recalculées) et best-effort -> n'échoue jamais le réentraînement.
        try:
            wc_bilan.update()
        except Exception as e:  # noqa: BLE001
            log.warning("bilan Coupe du Monde ignoré : %s", e)
        result["state"] = "done"
    except Exception as e:  # noqa: BLE001
        log.exception("Réentraînement en échec")
        result["state"] = "error"
        result["message"] = f"Échec du réentraînement : {e}"


def _join_cancellable(worker: threading.Thread, timeout: float, gen: int) -> str:
    """Attend `worker` jusqu'à `timeout`, en restant réactif à l'ANNULATION.

    Renvoie "done" (terminé), "timeout" (délai dépassé) ou "cancelled" (une
    annulation / un job plus récent a incrémenté la génération). On ne tient PAS le
    verrou pendant l'attente, pour que `cancel` puisse agir à tout moment.
    """
    end = time.monotonic() + timeout
    while worker.is_alive():
        if _generation != gen:
            return "cancelled"
        if time.monotonic() >= end:
            return "timeout"
        worker.join(0.5)
    return "done"


def _fail(gen: int, message: str, log_msg: str) -> None:
    """Passe proprement en « error » si ce job est toujours le plus récent."""
    with _lock:
        if gen != _generation:
            return
        _state.update(state="error", finished_at=_now(), message=message)
    log.error(log_msg)


def run(mode: str = "rapide") -> None:
    """Pilote la mise à jour en DEUX phases au budget SÉPARÉ. Le job ne peut jamais
    rester bloqué (chaque phase a son délai dur) et l'annulation est prise en compte
    immédiatement. Les workers (démons) abandonnés ne réécrivent jamais l'état.
    """
    global _generation
    with _lock:
        _generation += 1
        gen = _generation

    # --- Phase 1 : DONNÉES (budget réseau court) ---------------------------
    r1: dict = {}
    w1 = threading.Thread(target=_do_data, args=(r1,), daemon=True)
    w1.start()
    s1 = _join_cancellable(w1, config.REFRESH_DATA_TIMEOUT_S, gen)
    if s1 == "cancelled":
        return
    if s1 == "timeout":
        _fail(gen, "Délai dépassé : le réseau est trop lent. Réessaie plus tard "
                   "(les sources lentes sont mises en cache).",
              f"Mise à jour {mode} : phase données dépassée ({config.REFRESH_DATA_TIMEOUT_S:.0f}s)")
        return
    if r1.get("state") != "done":
        _fail(gen, r1.get("message", "Échec de la mise à jour."),
              f"Mise à jour {mode} : échec de la phase données")
        return
    msg = r1["message"]

    # --- Phase 2 : RÉENTRAÎNEMENT (budget large, complet seulement) ---------
    if mode == "complet":
        r2: dict = {}
        w2 = threading.Thread(target=_do_train, args=(r2,), daemon=True)
        w2.start()
        s2 = _join_cancellable(w2, config.REFRESH_TRAIN_TIMEOUT_S, gen)
        if s2 == "cancelled":
            return
        if s2 == "timeout":
            _fail(gen, "Réentraînement trop long : délai dépassé. Les données récentes "
                       "sont tout de même à jour ; réessaie le mode complet plus tard.",
                  f"Mise à jour complète : réentraînement dépassé ({config.REFRESH_TRAIN_TIMEOUT_S:.0f}s)")
            return
        if r2.get("state") != "done":
            _fail(gen, r2.get("message", "Échec du réentraînement."),
                  "Mise à jour complète : échec du réentraînement")
            return
        msg += " Modèles réentraînés."

    # --- Finalisation : caches vidés, horodatage persisté ------------------
    service.clear_caches()
    with _lock:
        if gen != _generation:
            return
        _persist_last_updated()
        _state.update(state="done", finished_at=_now(), message=msg)
    log.info("Mise à jour %s terminée : %s", mode, msg)


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
