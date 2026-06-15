"""Boucle de correction — apprentissage par l'expérience (Phase 3).

À partir des erreurs RÉALISÉES (prédictions réglées du journal), on apprend une
correction BORNÉE appliquée aux prochaines prédictions, sous garde-fous stricts :

  * Forme : une « température » T par domaine, qui affûte (T<1, corrige une
    sous-estimation des favoris / sous-confiance) ou aplatit (T>1) les probabilités
    1/X/2. Un seul paramètre -> robuste même sur peu de données, et BORNÉ : T ∈
    [T_MIN, T_MAX], donc la correction ne peut jamais dérailler.
  * Validation OBLIGATOIRE hors échantillon : on découpe le journal dans le TEMPS,
    on ajuste T sur le passé et on ne CONSERVE T que s'il améliore le RPS sur la
    tranche de validation (sinon T=1, aucune correction).
  * Anti-boucle : T est recalculé sur l'ENSEMBLE des erreurs accumulées (jamais en
    réaction à un seul match) et à partir des probabilités BRUTES du modèle (jamais
    de probabilités déjà corrigées) -> pas de rétroaction.
  * Réversible : un drapeau (`config.CORRECTION_ENABLED`) et l'état `enabled` de
    chaque domaine permettent de la désactiver ; T=1 = strictement neutre.

Le réentraînement complet du modèle continue en parallèle (il intègre les nouveaux
résultats) : cette couche n'est qu'un ajustement fin de calibration, validé.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from datetime import datetime, timezone

import numpy as np

from . import config, db, metrics

log = logging.getLogger("pipeline.correction")

T_MIN, T_MAX = 0.70, 1.40          # bornes dures de la température
_GRID = np.round(np.arange(T_MIN, T_MAX + 1e-9, 0.025), 3)
MIN_SETTLED = 100                  # en deçà : pas assez de recul -> aucune correction
VALID_FRACTION = 0.30              # part finale (chronologique) réservée à la validation
MIN_VALID = 30                     # taille minimale de la tranche de validation
RPS_MARGIN = 1e-4                  # amélioration minimale exigée (anti-bruit)

_PATH = os.path.join(config.MODELS_DIR, "correction.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def temper(probs: np.ndarray, t: float) -> np.ndarray:
    """Applique la température T à des probabilités (N,3) : p_i^(1/T) renormalisé.

    T<1 affûte (plus de confiance au favori), T>1 aplatit. T=1 = identité.
    """
    probs = np.clip(np.asarray(probs, dtype=float).reshape(-1, 3), 1e-12, 1.0)
    if abs(t - 1.0) < 1e-9:
        return probs / probs.sum(axis=1, keepdims=True)
    p = probs ** (1.0 / t)
    return p / p.sum(axis=1, keepdims=True)


def _best_temperature(probs: np.ndarray, outs: np.ndarray) -> float:
    """T (dans la grille bornée) minimisant le RPS sur (probs, outs)."""
    best_t, best_rps = 1.0, metrics.rps(temper(probs, 1.0), outs)
    for t in _GRID:
        r = metrics.rps(temper(probs, float(t)), outs)
        if r < best_rps - 1e-12:
            best_rps, best_t = r, float(t)
    return best_t


def fit_domain(records: list[dict]) -> dict:
    """Apprend + valide la correction d'un domaine à partir de ses prédictions réglées.

    `records` : liste de {date, probs:[ph,pd,pa], outcome}. Renvoie l'état de la
    correction : enabled, t, et les RPS de validation avant/après (traçabilité).
    """
    n = len(records)
    base = {"enabled": False, "t": 1.0, "n": n, "fitted_at": _now()}
    if n < MIN_SETTLED:
        base["reason"] = f"pas assez de données réglées ({n} < {MIN_SETTLED})"
        return base

    records = sorted(records, key=lambda r: r["date"] or "")
    probs = np.array([r["probs"] for r in records], dtype=float)
    outs = np.array([r["outcome"] for r in records], dtype=int)

    cut = int(n * (1.0 - VALID_FRACTION))
    if n - cut < MIN_VALID or cut < MIN_VALID:
        base["reason"] = "tranche de validation trop petite"
        return base

    # Ajustement sur le PASSÉ, validation sur la tranche finale (hors échantillon).
    t = _best_temperature(probs[:cut], outs[:cut])
    t = float(min(T_MAX, max(T_MIN, t)))           # borne dure (ceinture + bretelles)
    rps_base = metrics.rps(temper(probs[cut:], 1.0), outs[cut:])
    rps_corr = metrics.rps(temper(probs[cut:], t), outs[cut:])

    keep = (abs(t - 1.0) > 1e-9) and (rps_corr < rps_base - RPS_MARGIN)
    return {
        "enabled": bool(keep),
        "t": t if keep else 1.0,
        "n": n, "n_valid": n - cut,
        "rps_valid_base": round(float(rps_base), 5),
        "rps_valid_corr": round(float(rps_corr), 5),
        "fitted_at": _now(),
        "reason": ("validée hors échantillon" if keep
                   else "non conservée (n'améliore pas le RPS de validation)"),
    }


def fit(records_by_domain: dict[str, list[dict]]) -> dict:
    """État de correction pour chaque domaine fourni."""
    return {dom: fit_domain(recs) for dom, recs in records_by_domain.items()}


def refit_from_journal(conn=None) -> dict:
    """Recalcule la correction depuis le journal (prédictions réglées) et la PERSISTE.

    Apprise sur les probabilités BRUTES journalisées (anti-boucle). À appeler après
    le règlement, à chaque mise à jour des données.
    """
    own = conn is None
    conn = conn or db.connect()
    try:
        rows = conn.execute(
            "SELECT domain, match_date, p_home, p_draw, p_away, outcome "
            "FROM predictions_log WHERE status='settled' AND outcome IS NOT NULL"
        ).fetchall()
    finally:
        if own:
            conn.close()
    by_domain: dict[str, list[dict]] = {}
    for r in rows:
        by_domain.setdefault(r["domain"], []).append({
            "date": r["match_date"],
            "probs": [r["p_home"], r["p_draw"], r["p_away"]],
            "outcome": int(r["outcome"]),
        })
    state = fit(by_domain)
    save(state)
    enabled = [d for d, s in state.items() if s.get("enabled")]
    log.info("Correction recalculée : %s",
             (", ".join(f"{d}:T={state[d]['t']}" for d in enabled) if enabled
              else "aucune correction conservée"))
    return state


def save(state: dict) -> None:
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        load.cache_clear()
    except OSError as e:  # noqa: BLE001
        log.warning("écriture de la correction impossible : %s", e)


@functools.lru_cache(maxsize=1)
def load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def clear_cache() -> None:
    load.cache_clear()


def apply_to_pred(pred: dict, domain: str) -> dict:
    """Applique la correction validée du domaine aux probas 1/X/2 de `pred` (en place).

    Sans correction validée (ou drapeau désactivé), ne change RIEN. Borné par
    construction (T ∈ [T_MIN, T_MAX]).
    """
    if not config.CORRECTION_ENABLED:
        return pred
    # Idempotent : ne jamais corriger deux fois le même objet (évite un cumul T·T).
    if isinstance(pred.get("correction"), dict) and pred["correction"].get("applied"):
        return pred
    # Best-effort : une correction (état corrompu, valeur invalide…) ne doit JAMAIS
    # casser une prédiction. En cas de souci, on rend la prédiction inchangée.
    try:
        st = load().get(domain)
        if not st or not st.get("enabled"):
            return pred
        t = float(st.get("t", 1.0))
        if not (T_MIN <= t <= T_MAX) or abs(t - 1.0) < 1e-9:
            return pred
        corr = temper([[pred["p_home_win"], pred["p_draw"], pred["p_away_win"]]], t)[0]
        pred["p_home_win"] = round(float(corr[0]), 4)
        pred["p_draw"] = round(float(corr[1]), 4)
        pred["p_away_win"] = round(float(corr[2]), 4)
        pred["correction"] = {"applied": True, "t": t, "domain": domain}
    except Exception as e:  # noqa: BLE001
        log.debug("correction non appliquée (ignorée) : %s", e)
    return pred
