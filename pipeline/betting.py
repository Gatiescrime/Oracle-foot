"""Moteur de backtest de PARIS — la métrique de VÉRITÉ du projet.

Le RPS dit si nos probabilités sont bonnes ; le pari dit si elles sont
EXPLOITABLES face au marché. On rejoue l'histoire dans l'ordre du temps
(walk-forward, comme `backtest.py`) : à chaque étape on n'entraîne QUE sur le
passé et on prédit le bloc suivant, jamais vu.

ANTI-FUITE — règle d'or non négociable :
  * Décision de parier et PRIX D'ENTRÉE = cotes d'OUVERTURE (pré-match) uniquement.
  * Les cotes de CLÔTURE (Pinnacle PSC) ne servent QU'À MESURER le CLV
    (Closing Line Value) a posteriori. Jamais en entrée de décision.

On simule :
  * détection de « value » : on parie si proba_modèle × cote_entrée − 1 > seuil ;
  * mise à PLAT et mise de KELLY fractionné (1/4, 1/2), commission paramétrable ;
  * suivi de bankroll : ROI, rendement (yield), drawdown max, taux de réussite ;
  * CLV : pour chaque pari, prix d'entrée vs prix de clôture. Le CLV moyen positif
    est LE signe d'un edge réel et durable.

Marchés couverts par les cotes football-data : 1X2 et Over/Under 2,5 buts.
(Pas de cotes BTTS chez football-data -> BTTS traité en calibration ailleurs.)
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import config, db, metrics
from .calibration import ProbabilityCalibrator
from .dixon_coles import fit_dixon_coles
from .ensemble import EnsemblePredictor
from .xgb_model import FEATURE_COLS, XGBPoissonModel
from .backtest import _dc_matches, _load

log = logging.getLogger("pipeline.betting")

_OUTCOME_LABELS = ("home", "draw", "away")


# ---------------------------------------------------------------------------
# 1) Collecte walk-forward : une « sélection » pariable par issue de marché.
# ---------------------------------------------------------------------------
def _odds_by_match(conn, domain: str) -> pd.DataFrame:
    """Toutes les cotes (ouverture + clôture) indexées par match_id."""
    cols = ("match_id, odds_home, odds_draw, odds_away, "
            "odds_max_home, odds_max_draw, odds_max_away, "
            "odds_open_over25, odds_open_under25, "
            "odds_close_home, odds_close_draw, odds_close_away, "
            "odds_close_over25, odds_close_under25")
    df = pd.read_sql_query(
        f"SELECT {cols} FROM matches WHERE domain = ?", conn, params=(domain,))
    return df.set_index("match_id")


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(f) or f <= 1.0) else f


def collect_selections(conn, domain: str, n_folds: int = 6,
                       min_train_frac: float = 0.5) -> list[dict]:
    """Rejoue l'histoire et renvoie la liste chronologique des sélections pariables.

    Chaque sélection : date, league, market, model_prob, dec_odds (OUVERTURE),
    max_odds (meilleur prix), close_odds (clôture, pour CLV), won (issue réelle).
    """
    df = _load(conn, domain)
    n = len(df)
    if n < 200:
        log.warning("Backtest paris %s : trop peu de matchs (%d)", domain, n)
        return []
    odds = _odds_by_match(conn, domain)

    start = int(n * min_train_frac)
    bounds = np.linspace(start, n, n_folds + 1, dtype=int)

    hist_raw: list[np.ndarray] = []
    hist_out: list[int] = []
    selections: list[dict] = []

    for k in range(n_folds):
        lo, hi = bounds[k], bounds[k + 1]
        if hi <= lo:
            continue
        train = df.iloc[:lo]
        test = df.iloc[lo:hi]

        dc = fit_dixon_coles(_dc_matches(train), verbose=False)
        xgb = XGBPoissonModel().fit(train) if len(train) >= 300 else None
        calibrator = None
        if len(hist_raw) >= 300:
            calibrator = ProbabilityCalibrator().fit(
                np.array(hist_raw), np.array(hist_out))
        predictor = EnsemblePredictor(dc, xgb, calibrator, domain)

        for r in test.itertuples(index=False):
            feat = {c: getattr(r, c, np.nan) for c in
                    FEATURE_COLS + ["home_played", "away_played"]}
            home, away = r.home_team_id, r.away_team_id
            neutral = bool(r.neutral)
            pred = predictor.predict(home, away, neutral, feat)

            raw = np.array([pred["p_home_win"], pred["p_draw"], pred["p_away_win"]])
            outcome = metrics.outcome_from_goals(int(r.home_goals), int(r.away_goals))
            hist_raw.append(raw)
            hist_out.append(outcome)

            if r.match_id not in odds.index:
                continue
            o = odds.loc[r.match_id]
            league = r.competition
            date = r.date

            # --- marché 1X2 ---
            probs_1x2 = [pred["p_home_win"], pred["p_draw"], pred["p_away_win"]]
            dec_1x2 = [_num(o.odds_home), _num(o.odds_draw), _num(o.odds_away)]
            max_1x2 = [_num(o.odds_max_home), _num(o.odds_max_draw), _num(o.odds_max_away)]
            close_1x2 = [_num(o.odds_close_home), _num(o.odds_close_draw),
                         _num(o.odds_close_away)]
            for i in range(3):
                if dec_1x2[i] is None:
                    continue
                selections.append({
                    "date": date, "league": league, "domain": domain,
                    "market": "1X2", "selection": _OUTCOME_LABELS[i],
                    "model_prob": float(probs_1x2[i]),
                    "dec_odds": dec_1x2[i],
                    "max_odds": max_1x2[i] if max_1x2[i] else dec_1x2[i],
                    "close_odds": close_1x2[i],
                    "won": bool(outcome == i),
                })

            # --- marché Over/Under 2,5 buts ---
            total = int(r.home_goals) + int(r.away_goals)
            p_over = float(pred["p_over_2_5"])
            ou = [
                ("over",  p_over,       _num(o.odds_open_over25),  _num(o.odds_close_over25),  total > 2),
                ("under", 1.0 - p_over, _num(o.odds_open_under25), _num(o.odds_close_under25), total <= 2),
            ]
            for label, p, dec, close, won in ou:
                if dec is None:
                    continue
                selections.append({
                    "date": date, "league": league, "domain": domain,
                    "market": "OU25", "selection": label,
                    "model_prob": p, "dec_odds": dec, "max_odds": dec,
                    "close_odds": close, "won": bool(won),
                })

    selections.sort(key=lambda s: (s["date"], s["market"], s["selection"]))
    return selections


# ---------------------------------------------------------------------------
# 2) Détection de value + simulation de capital.
# ---------------------------------------------------------------------------
def _kelly_fraction(p: float, dec_odds: float) -> float:
    """Fraction de Kelly pleine : f* = (b·p − q) / b, avec b = cote − 1."""
    b = dec_odds - 1.0
    if b <= 0:
        return 0.0
    return (b * p - (1.0 - p)) / b


def find_bets(selections: list[dict], *, edge: float,
              price: str = "open") -> list[dict]:
    """Sélectionne les paris de value : proba × cote_entrée − 1 > seuil.

    `price` = 'open' (cote d'ouverture, défaut, sans fuite) ou 'max' (meilleur prix).
    La cote de clôture n'est JAMAIS utilisée ici (uniquement pour le CLV ensuite).
    """
    bets = []
    for s in selections:
        dec = s["max_odds"] if price == "max" else s["dec_odds"]
        if dec is None:
            continue
        e = s["model_prob"] * dec - 1.0
        if e > edge:
            b = dict(s)
            b["entry_odds"] = dec
            b["edge"] = e
            b["kelly_f"] = _kelly_fraction(s["model_prob"], dec)
            bets.append(b)
    bets.sort(key=lambda x: (x["date"], x["market"], x["selection"]))
    return bets


def simulate(bets: list[dict], *, mode: str = "flat",
             kelly_fraction: float | None = None,
             commission: float | None = None,
             bankroll0: float | None = None,
             flat_stake: float | None = None,
             kelly_cap: float | None = None) -> dict:
    """Simule le capital sur les paris (ordre chronologique).

    mode = 'flat' (mise fixe) ou 'kelly' (mise = bankroll × fraction × f*, plafonnée).
    Renvoie ROI, yield, drawdown max, taux de réussite, bankroll finale, CLV.
    """
    commission = config.BET_COMMISSION if commission is None else commission
    bankroll0 = config.BET_BANKROLL_0 if bankroll0 is None else bankroll0
    flat_stake = config.BET_STAKE_FLAT if flat_stake is None else flat_stake
    kelly_cap = config.BET_KELLY_CAP if kelly_cap is None else kelly_cap
    kelly_fraction = (config.BET_KELLY_FRACTION
                      if kelly_fraction is None else kelly_fraction)

    bankroll = bankroll0
    peak = bankroll0
    max_dd = 0.0
    staked_total = 0.0
    wins = 0

    for b in bets:
        if mode == "kelly":
            stake = bankroll * kelly_fraction * max(b["kelly_f"], 0.0)
            stake = min(stake, kelly_cap, max(bankroll, 0.0))
        else:
            stake = flat_stake
        if stake <= 0:
            continue
        staked_total += stake
        if b["won"]:
            bankroll += stake * (b["entry_odds"] - 1.0) * (1.0 - commission)
            wins += 1
        else:
            bankroll -= stake
        peak = max(peak, bankroll)
        if peak > 0:
            max_dd = max(max_dd, (peak - bankroll) / peak)

    n = len(bets)
    profit = bankroll - bankroll0
    return {
        "mode": mode if mode != "kelly" else f"kelly_{kelly_fraction:g}",
        "n_bets": n,
        "staked": round(staked_total, 2),
        "profit": round(profit, 2),
        "bankroll_final": round(bankroll, 2),
        "roi": round(profit / bankroll0, 4) if bankroll0 else 0.0,
        "yield": round(profit / staked_total, 4) if staked_total else 0.0,
        "max_drawdown": round(max_dd, 4),
        "hit_rate": round(wins / n, 4) if n else 0.0,
        **clv_stats(bets),
    }


# ---------------------------------------------------------------------------
# 3) CLV — Closing Line Value (mesure a posteriori de l'edge réel).
# ---------------------------------------------------------------------------
def clv_stats(bets: list[dict]) -> dict:
    """CLV : prix d'entrée vs prix de clôture.

    CLV% = cote_entrée / cote_clôture − 1. Positif = on a obtenu un meilleur prix
    que la clôture (« battre la ligne »), signe d'un edge durable. On rapporte le
    CLV moyen et le taux de paris qui battent la clôture.
    """
    clvs = []
    beat = 0
    for b in bets:
        close = b.get("close_odds")
        if close is None or close <= 1.0:
            continue
        clv = b["entry_odds"] / close - 1.0
        clvs.append(clv)
        if clv > 0:
            beat += 1
    if not clvs:
        return {"clv_mean": None, "clv_beat_rate": None, "n_clv": 0}
    return {
        "clv_mean": round(float(np.mean(clvs)), 4),
        "clv_beat_rate": round(beat / len(clvs), 4),
        "n_clv": len(clvs),
    }


# ---------------------------------------------------------------------------
# 4) Ventilation par marché et par ligue (mise à plat, yield comparable).
# ---------------------------------------------------------------------------
def _breakdown(bets: list[dict], key: str, **sim_kw) -> dict:
    groups: dict[str, list[dict]] = {}
    for b in bets:
        groups.setdefault(b[key], []).append(b)
    out = {}
    for g, gb in sorted(groups.items()):
        out[g] = simulate(gb, mode="flat", **sim_kw)
    return out


# ---------------------------------------------------------------------------
# 5) Orchestration.
# ---------------------------------------------------------------------------
def run_backtest(conn, domain: str, *, edge: float | None = None,
                 n_folds: int = 6, price: str = "open") -> dict:
    edge = config.BET_EDGE_THRESHOLD if edge is None else edge
    # Pas de cotes pour ce domaine (ex. sélections) -> inutile de rejouer l'histoire.
    has_odds = conn.execute(
        "SELECT 1 FROM matches WHERE domain=? AND odds_home IS NOT NULL LIMIT 1",
        (domain,)).fetchone()
    if not has_odds:
        log.info("Aucune cote pour le domaine %s : backtest paris ignoré.", domain)
        return {}
    selections = collect_selections(conn, domain, n_folds=n_folds)
    if not selections:
        return {}
    bets = find_bets(selections, edge=edge, price=price)
    res = {
        "domain": domain,
        "edge_threshold": edge,
        "price_source": price,
        "n_selections": len(selections),
        "n_bets": len(bets),
        "staking": {
            "flat": simulate(bets, mode="flat"),
            "kelly_0.25": simulate(bets, mode="kelly", kelly_fraction=0.25),
            "kelly_0.5": simulate(bets, mode="kelly", kelly_fraction=0.5),
        },
        "by_market": _breakdown(bets, "market"),
        "by_league": _breakdown(bets, "league"),
    }
    return res


def run_all(db_path: str | None = None, edge: float | None = None) -> dict:
    conn = db.connect(db_path)
    res = {}
    for domain in (config.DOMAIN_CLUB, config.DOMAIN_INTL):
        log.info("Backtest paris %s…", domain)
        r = run_backtest(conn, domain, edge=edge)
        if r:
            res[domain] = r
    conn.close()
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import json
    print(json.dumps(run_all(), indent=2, ensure_ascii=False))
