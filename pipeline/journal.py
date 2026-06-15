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

import numpy as np

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


def _agg(rows: list) -> dict:
    """Métriques agrégées d'un lot de prédictions réglées."""
    probs = np.array([[r["p_home"], r["p_draw"], r["p_away"]] for r in rows], dtype=float)
    outs = np.array([r["outcome"] for r in rows], dtype=int)
    rpss = [r["rps"] for r in rows if r["rps"] is not None]
    briers = [r["brier"] for r in rows if r["brier"] is not None]
    clvs = [r["clv"] for r in rows if r["clv"] is not None]
    out = {
        "n": len(rows),
        "accuracy": round(sum(r["correct_1x2"] for r in rows) / len(rows), 4),
        "rps": round(float(np.mean(rpss)), 4) if rpss else None,
        "brier": round(float(np.mean(briers)), 4) if briers else None,
    }
    if clvs:
        out["clv_mean"] = round(float(np.mean(clvs)), 4)
        out["clv_n"] = len(clvs)
        out["clv_beat_rate"] = round(float(np.mean([1.0 if c > 0 else 0.0 for c in clvs])), 4)
    # ROI : pari à plat sur l'issue prédite, à la cote captée (si dispo). Sinon None.
    staked = profit = 0.0
    for r in rows:
        odd = [r["market_home"], r["market_draw"], r["market_away"]][r["predicted_outcome"]]
        if odd and odd > 1.0:
            staked += 1.0
            profit += (float(odd) - 1.0) if r["correct_1x2"] else -1.0
    if staked > 0:
        out["roi"] = round(profit / staked, 4)
        out["roi_n_bets"] = int(staked)
    return out


def live_track_record(conn=None) -> dict:
    """Performance RÉELLE issue du journal (prédictions réglées) — pour la page
    Track record. Agrège : taux de réussite 1X2, RPS, Brier, calibration, RPS dans le
    temps (par mois), et par compétition ; ROI/CLV si des cotes ont été captées.
    """
    own = conn is None
    conn = conn or db.connect()
    try:
        settled = conn.execute(
            "SELECT * FROM predictions_log WHERE status='settled'").fetchall()
        n_pending = conn.execute(
            "SELECT COUNT(*) FROM predictions_log WHERE status='pending'").fetchone()[0]
    finally:
        if own:
            conn.close()

    if not settled:
        return {"available": False, "n_settled": 0, "n_pending": int(n_pending)}

    probs = np.array([[r["p_home"], r["p_draw"], r["p_away"]] for r in settled], dtype=float)
    outs = np.array([r["outcome"] for r in settled], dtype=int)

    # Par compétition (trié par effectif décroissant).
    comps: dict[str, list] = {}
    for r in settled:
        comps.setdefault(r["competition"], []).append(r)
    by_competition = sorted(
        [{"competition": c, **_agg(rs)} for c, rs in comps.items()],
        key=lambda d: d["n"], reverse=True)

    # RPS dans le temps : par mois (date du match).
    months: dict[str, list] = {}
    for r in settled:
        months.setdefault((r["match_date"] or "")[:7], []).append(r)
    over_time = [
        {"period": m, "n": len(rs),
         "rps": round(float(np.mean([x["rps"] for x in rs if x["rps"] is not None])), 4)
         if any(x["rps"] is not None for x in rs) else None}
        for m, rs in sorted(months.items()) if m]

    dates = sorted(r["match_date"] for r in settled if r["match_date"])
    return {
        "available": True,
        "n_settled": len(settled),
        "n_pending": int(n_pending),
        "since": dates[0] if dates else None,
        "until": dates[-1] if dates else None,
        **_agg(settled),
        "calibration": metrics.calibration_table(probs, outs),
        "by_competition": by_competition,
        "over_time": over_time,
    }


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
