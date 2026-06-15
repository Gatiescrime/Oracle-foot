"""Journal des prédictions — apprentissage par l'expérience (Phase 1).

Idée : pour mesurer la performance RÉELLE du modèle (et plus tard apprendre une
correction), on enregistre chaque prédiction rattachable à un vrai match AVANT que
le résultat soit connu, puis on la « règle » quand le vrai score arrive.

Garde-fou anti-fuite (non négociable) :
  - on ne journalise QUE des matchs présents au CALENDRIER (`fixtures`), donc à venir ;
  - on REFUSE de journaliser si le résultat est déjà connu (présent dans `matches`) ;
  - au moment de journaliser, AUCUNE colonne de résultat n'est renseignée (statut
    'pending'). Les résultats et métriques ne sont remplis qu'au RÈGLEMENT, à partir
    des vrais matchs joués.

Couverture : les prédictions sont rattachées via la table `fixtures` (calendrier),
qui couvre les sélections (Coupe du Monde incluse). Les matchs hors calendrier ne
sont pas journalisés (analyses hypothétiques).
"""

from __future__ import annotations

import functools
import json
import logging
import os
from datetime import datetime, timezone

from . import config, db, metrics

log = logging.getLogger("pipeline.journal")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@functools.lru_cache(maxsize=4)
def model_version(domain: str) -> str:
    """Version du modèle ayant produit la prédiction (date d'entraînement du domaine).

    Sert à regrouper les prédictions par génération de modèle : un réentraînement
    change la version, les anciennes prédictions gardent la leur.
    """
    tag = "club" if domain == config.DOMAIN_CLUB else "intl"
    path = os.path.join(config.MODELS_DIR, f"meta_{tag}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return str(json.load(f).get("trained_on") or "inconnu")
    except Exception:  # noqa: BLE001
        return "inconnu"


def clear_version_cache() -> None:
    model_version.cache_clear()


def maybe_log(pred: dict, domain: str, competition: str, home_id: str, away_id: str,
              neutral: bool, use_qualitative: bool) -> bool:
    """Journalise la prédiction si elle correspond à un vrai match À VENIR.

    Renvoie True si une ligne a été (ou était déjà) enregistrée. Best-effort : toute
    erreur est avalée (ne casse JAMAIS la prédiction servie à l'utilisateur).
    """
    if not config.PREDICTION_LOG_ENABLED:
        return False
    try:
        conn = db.connect()
        try:
            # 1) le match doit être au calendrier (donc à venir) -> date de référence.
            fx = conn.execute(
                "SELECT date, competition FROM fixtures "
                "WHERE domain=? AND home_team_id=? AND away_team_id=?",
                (domain, home_id, away_id)).fetchone()
            if fx is None:
                return False
            match_date = fx["date"]
            # 2) ANTI-FUITE : refuser si le résultat est déjà connu.
            if conn.execute(
                "SELECT 1 FROM matches WHERE domain=? AND home_team_id=? "
                "AND away_team_id=? AND date=?",
                (domain, home_id, away_id, match_date)).fetchone() is not None:
                return False
            ml = pred.get("most_likely_score") or [None, None]
            conn.execute(
                """INSERT OR IGNORE INTO predictions_log
                   (created_at, model_version, domain, competition, home_team_id,
                    away_team_id, match_date, neutral, use_qualitative,
                    p_home, p_draw, p_away, exp_home_goals, exp_away_goals,
                    ml_home, ml_away, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')""",
                (_now(), model_version(domain), domain, fx["competition"] or competition,
                 home_id, away_id, match_date, 1 if neutral else 0,
                 1 if use_qualitative else 0,
                 float(pred["p_home_win"]), float(pred["p_draw"]), float(pred["p_away_win"]),
                 _f(pred.get("exp_home_goals")), _f(pred.get("exp_away_goals")),
                 _i(ml[0]), _i(ml[1])))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — jamais de crash côté prédiction
        log.debug("journalisation impossible : %s", e)
        return False


def settle_pending(conn=None) -> int:
    """Règle les prédictions en attente dont le résultat réel est désormais connu.

    Pour chaque ligne 'pending', cherche le match joué correspondant dans `matches`
    (mêmes équipes, date proche), puis calcule les métriques (bon/mauvais 1X2, Brier,
    RPS, et CLV si une cote captée + une cote de clôture existent). Renvoie le nombre
    de prédictions réglées.
    """
    own = conn is None
    conn = conn or db.connect()
    settled = 0
    try:
        rows = conn.execute(
            "SELECT * FROM predictions_log WHERE status='pending'").fetchall()
        for r in rows:
            m = conn.execute(
                """SELECT home_goals, away_goals, odds_close_home, odds_close_draw,
                          odds_close_away
                     FROM matches
                    WHERE domain=? AND home_team_id=? AND away_team_id=?
                      AND ABS(julianday(date) - julianday(?)) <= 3
                    ORDER BY ABS(julianday(date) - julianday(?)) LIMIT 1""",
                (r["domain"], r["home_team_id"], r["away_team_id"],
                 r["match_date"], r["match_date"])).fetchone()
            if m is None:
                continue
            hg, ag = int(m["home_goals"]), int(m["away_goals"])
            probs = [r["p_home"], r["p_draw"], r["p_away"]]
            outcome = metrics.outcome_from_goals(hg, ag)
            pred_out = int(max(range(3), key=lambda i: probs[i]))
            brier = float(sum((probs[i] - (1.0 if i == outcome else 0.0)) ** 2
                              for i in range(3)))
            rps = float(metrics.rps([probs], [outcome]))
            clv = _clv(r, m, pred_out)
            conn.execute(
                """UPDATE predictions_log
                      SET status='settled', settled_at=?, actual_home_goals=?,
                          actual_away_goals=?, outcome=?, predicted_outcome=?,
                          correct_1x2=?, brier=?, rps=?, clv=?
                    WHERE id=?""",
                (_now(), hg, ag, outcome, pred_out,
                 1 if pred_out == outcome else 0, brier, rps, clv, r["id"]))
            settled += 1
        conn.commit()
        if settled:
            log.info("Journal : %d prédiction(s) réglée(s)", settled)
    finally:
        if own:
            conn.close()
    return settled


def _clv(row, match, pred_out: int) -> float | None:
    """CLV de l'issue prédite : cote captée / cote de clôture − 1 (None si absent)."""
    entry = [row["market_home"], row["market_draw"], row["market_away"]][pred_out]
    close = [match["odds_close_home"], match["odds_close_draw"],
             match["odds_close_away"]][pred_out]
    if entry and close and entry > 1.0 and close > 1.0:
        return round(float(entry) / float(close) - 1.0, 4)
    return None


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None
