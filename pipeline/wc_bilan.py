"""Bilan honnête de la Coupe du Monde : prédit (pré-match) vs réel.

Pour chaque match de la Coupe du Monde 2026 DÉJÀ JOUÉ, on calcule ce que le modèle
aurait prédit en n'utilisant QUE les informations connues AVANT le coup d'envoi, puis
on compare au résultat réel.

RÈGLE D'OR (zéro fuite) : la prédiction d'un match du jour `d` est faite par un modèle
(Dixon-Coles + XGBoost + calibration) reconstruit UNIQUEMENT à partir des matchs
internationaux STRICTEMENT antérieurs à `d`. Jamais le match lui-même, jamais un match
postérieur. C'est ce qui rend la comparaison honnête.

C'est une mesure walk-forward, distincte du JOURNAL des prédictions live (Phase 1-4) :
ici on rejoue l'histoire (disponible tout de suite pour tout match joué) ; là-bas on
note les prédictions que l'utilisateur fait réellement, au fil de l'eau.

Coût maîtrisé : un seul réajustement par DATE de match (les matchs d'un même jour
partagent le même passé), et les résultats par match sont MIS EN CACHE (stables une
fois calculés). À chaque appel, seules les nouvelles dates sont recalculées.
"""

from __future__ import annotations

import json
import logging
import os

import numpy as np

from . import config, db, metrics
from .backtest import _dc_matches, _load
from .dixon_coles import fit_dixon_coles
from .ensemble import EnsemblePredictor
from .train import _fit_calibrator
from .xgb_model import FEATURE_COLS, XGBPoissonModel

log = logging.getLogger("pipeline.wc_bilan")

WC = config.WORLD_CUP_COMPETITION          # "FIFA World Cup"
WC_SINCE = "2026-01-01"                     # édition 2026
_PATH = os.path.join(config.DATA_DIR, "wc_bilan.json")
_MIN_TRAIN = 300                           # en deçà, pas de modèle fiable -> match ignoré


def _train_before(df, d):
    """Matchs STRICTEMENT antérieurs à la date `d` (cœur de l'anti-fuite)."""
    return df[df["date"] < d]


def _host_bonus(home_name: str, away_name: str) -> tuple[float, float]:
    """Effet pays hôte (FIXE, connu avant le match -> aucune fuite), comme en live :
    bonus de buts pour l'hôte du Mondial (USA/Canada/Mexique). Pour rester cohérent
    avec ce que le site affiche, le bilan applique le même bonus."""
    b = config.HOST_GOAL_LOG_BONUS
    home_host = home_name in config.WORLD_CUP_HOSTS
    away_host = away_name in config.WORLD_CUP_HOSTS
    if home_host and not away_host:
        return b, 0.0
    if away_host and not home_host:
        return 0.0, b
    return 0.0, 0.0


def _predict_match(pred: EnsemblePredictor, row, host_log_bonus=(0.0, 0.0)) -> dict:
    feat = {c: getattr(row, c, np.nan) for c in FEATURE_COLS + ["home_played", "away_played"]}
    return pred.predict(row.home_team_id, row.away_team_id, bool(row.neutral), feat,
                        host_log_bonus=host_log_bonus)


def compute(conn=None, cache: dict | None = None) -> dict:
    """Calcule (ou complète) le bilan CdM. Réutilise `cache` pour les matchs déjà
    évalués (stables) et ne réajuste un modèle que pour les NOUVELLES dates.
    """
    own = conn is None
    conn = conn or db.connect()
    try:
        df = _load(conn, config.DOMAIN_INTL)            # features intl, triées par date
        names = {r["team_id"]: r["canonical_name"]
                 for r in conn.execute("SELECT team_id, canonical_name FROM teams").fetchall()}
    finally:
        if own:
            conn.close()

    wc = df[(df["competition"] == WC) & (df["date"].astype(str) >= WC_SINCE)].copy()
    if wc.empty:
        return {"available": False, "n_matches": 0}

    cached = {m["match_id"]: m for m in (cache or {}).get("matches", [])}
    wc_dates = sorted(wc["date"].astype(str).unique())

    # Calibrateur LEAK-FREE commun : ajusté sur les matchs antérieurs au TOUT PREMIER
    # match de CdM (donc avant tous les matchs évalués). Évite un réajustement par date.
    earliest = wc_dates[0]
    pre = _train_before(df, earliest)
    calibrator = _fit_calibrator(pre, config.DOMAIN_INTL) if len(pre) >= _MIN_TRAIN else None

    out_matches: list[dict] = []
    for d in wc_dates:
        day = wc[wc["date"].astype(str) == d]
        todo = [r for r in day.itertuples(index=False) if _mid(r, d) not in cached]
        if not todo:
            out_matches.extend(cached[_mid(r, d)] for r in day.itertuples(index=False))
            continue
        train = _train_before(df, d)                    # ANTI-FUITE : uniquement < d
        if len(train) < _MIN_TRAIN:
            continue
        dc = fit_dixon_coles(_dc_matches(train), verbose=False,
                             **config.dc_params(config.DOMAIN_INTL))
        xgb = XGBPoissonModel().fit(train) if len(train) >= _MIN_TRAIN else None
        pred = EnsemblePredictor(dc, xgb, calibrator, config.DOMAIN_INTL)
        for r in day.itertuples(index=False):
            mid = _mid(r, d)
            if mid in cached:
                out_matches.append(cached[mid]); continue
            bonus = _host_bonus(names.get(r.home_team_id, ""), names.get(r.away_team_id, ""))
            p = _predict_match(pred, r, host_log_bonus=bonus)
            out_matches.append(_match_entry(p, r, d, names))

    out_matches.sort(key=lambda m: (m["date"], m["home"]))
    return _summarize(out_matches)


def _mid(row, d: str) -> str:
    return f"{d}|{row.home_team_id}|{row.away_team_id}"


def _match_entry(p: dict, row, d: str, names: dict) -> dict:
    probs = [p["p_home_win"], p["p_draw"], p["p_away_win"]]
    hg, ag = int(row.home_goals), int(row.away_goals)
    outcome = metrics.outcome_from_goals(hg, ag)
    pred_out = int(max(range(3), key=lambda i: probs[i]))
    brier = float(sum((probs[i] - (1.0 if i == outcome else 0.0)) ** 2 for i in range(3)))
    ml = p.get("most_likely_score") or [None, None]
    return {
        "match_id": _mid(row, d), "date": d,
        "home": names.get(row.home_team_id, row.home_team_id),
        "away": names.get(row.away_team_id, row.away_team_id),
        "neutral": bool(row.neutral),
        "p_home": probs[0], "p_draw": probs[1], "p_away": probs[2],
        "exp_home_goals": p.get("exp_home_goals"), "exp_away_goals": p.get("exp_away_goals"),
        "ml_home": ml[0], "ml_away": ml[1],
        "actual_home": hg, "actual_away": ag,
        "outcome": outcome, "predicted_outcome": pred_out,
        "correct_1x2": int(pred_out == outcome),
        "brier": round(brier, 4),
        "rps": round(float(metrics.rps([probs], [outcome])), 4),
    }


def _summarize(matches: list[dict]) -> dict:
    if not matches:
        return {"available": False, "n_matches": 0}
    probs = np.array([[m["p_home"], m["p_draw"], m["p_away"]] for m in matches])
    outs = np.array([m["outcome"] for m in matches])
    return {
        "available": True,
        "n_matches": len(matches),
        "accuracy": round(float(np.mean([m["correct_1x2"] for m in matches])), 4),
        "rps": round(float(np.mean([m["rps"] for m in matches])), 4),
        "brier": round(float(np.mean([m["brier"] for m in matches])), 4),
        "calibration": metrics.calibration_table(probs, outs),
        "avg_pred_home_goals": round(float(np.mean([m["exp_home_goals"] for m in matches])), 2),
        "avg_pred_away_goals": round(float(np.mean([m["exp_away_goals"] for m in matches])), 2),
        "avg_real_home_goals": round(float(np.mean([m["actual_home"] for m in matches])), 2),
        "avg_real_away_goals": round(float(np.mean([m["actual_away"] for m in matches])), 2),
        "matches": matches,
    }


def load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"available": False, "n_matches": 0}


def save(bilan: dict) -> None:
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(bilan, f, ensure_ascii=False)
    except OSError as e:  # noqa: BLE001
        log.warning("écriture du bilan CdM impossible : %s", e)


def update(conn=None) -> dict:
    """Recalcule le bilan en réutilisant le cache disque (incrémental) et le persiste."""
    bilan = compute(conn, cache=load())
    save(bilan)
    log.info("Bilan CdM : %d match(s) évalué(s)", bilan.get("n_matches", 0))
    return bilan


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    b = update()
    print(json.dumps({k: v for k, v in b.items() if k != "matches"},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
