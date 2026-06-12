"""Phase 6 — Simulation Monte-Carlo de la Coupe du Monde 2026.

On rejoue le tournoi des milliers de fois pour estimer, pour chaque équipe, sa
probabilité de sortir des poules, d'atteindre chaque tour, et de gagner le titre.

Méthode :
  1. On déduit les 12 groupes de 4 à partir du calendrier réel (`fixtures`) :
     les équipes qui se rencontrent en phase de groupes forment un groupe.
  2. Pour CHAQUE affrontement possible, on précalcule UNE fois la matrice des scores
     du modèle (terrain neutre). Simuler un match = tirer un score dans cette matrice
     (rapide). On évite ainsi des millions d'appels au modèle.
  3. Phase de groupes : round-robin, 3/1/0 points, départage diff. de buts puis buts
     marqués. Qualifiés = 2 premiers de chaque groupe + 8 meilleurs troisièmes (format
     officiel à 48 équipes → 32 qualifiés).
  4. Phase à élimination directe : bracket à têtes de série (approximation — voir la
     note d'honnêteté ci-dessous) jusqu'au titre.

Note d'honnêteté : l'appariement EXACT des 8es dépend des positions de groupe
qualifiées selon une grille officielle complexe ; on utilise ici un bracket à têtes
de série (le mieux classé affronte le moins bien classé). Les probabilités de titre
sont donc indicatives, cohérentes avec la force estimée, mais pas l'exact tirage FIFA.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np
import pandas as pd

from . import db, service

log = logging.getLogger("pipeline.simulate")

WC_COMPETITION = "FIFA World Cup"


def _load_group_fixtures(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT th.canonical_name AS home, ta.canonical_name AS away
           FROM fixtures f
           JOIN teams th ON th.team_id = f.home_team_id
           JOIN teams ta ON ta.team_id = f.away_team_id
           WHERE f.competition = ?""", conn, params=(WC_COMPETITION,))


def _infer_groups(fixtures: pd.DataFrame) -> list[list[str]]:
    """Union-find : les équipes reliées par des matchs de poule forment un groupe."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for r in fixtures.itertuples(index=False):
        union(r.home, r.away)

    groups: dict[str, list[str]] = defaultdict(list)
    for t in parent:
        groups[find(t)].append(t)
    return [sorted(g) for g in groups.values()]


class _MatchSampler:
    """Précalcule et met en cache la matrice des scores de chaque affrontement."""

    def __init__(self, predict_fn=None):
        self._predict = predict_fn or (
            lambda h, a: service.predict(WC_COMPETITION, h, a, neutral=True))
        self._cache: dict[tuple, tuple] = {}

    def _matrix(self, home, away):
        key = (home, away)
        if key not in self._cache:
            mat = np.array(self._predict(home, away)["score_matrix"], dtype=float)
            mat = mat / mat.sum()
            self._cache[key] = (mat.ravel(), mat.shape[1])
        return self._cache[key]

    def sample(self, home, away, rng) -> tuple[int, int]:
        flat, ncols = self._matrix(home, away)
        idx = int(np.searchsorted(np.cumsum(flat), rng.random()))
        idx = min(idx, len(flat) - 1)
        return divmod(idx, ncols)


def _rank_group(table: dict) -> list[str]:
    """Classe un groupe par points, puis diff. de buts, puis buts marqués."""
    return sorted(table, key=lambda t: (table[t]["pts"], table[t]["gd"],
                                        table[t]["gf"]), reverse=True)


def _simulate_once(groups, sampler, rng) -> dict:
    """Une simulation complète du tournoi. Renvoie les tours atteints par équipe."""
    reached = {}              # team -> tour max atteint (codé en entier)
    qualified, thirds = [], []

    for g in groups:
        table = {t: {"pts": 0, "gd": 0, "gf": 0} for t in g}
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                h, a = g[i], g[j]
                hg, ag = sampler.sample(h, a, rng)
                table[h]["gf"] += hg; table[a]["gf"] += ag
                table[h]["gd"] += hg - ag; table[a]["gd"] += ag - hg
                if hg > ag: table[h]["pts"] += 3
                elif hg < ag: table[a]["pts"] += 3
                else: table[h]["pts"] += 1; table[a]["pts"] += 1
        ranked = _rank_group(table)
        for t in g:
            reached[t] = 0                      # éliminé en poules par défaut
        qualified.extend(ranked[:2])
        if len(ranked) >= 3:
            thirds.append((ranked[2], table[ranked[2]]))

    # 8 meilleurs troisièmes
    thirds.sort(key=lambda x: (x[1]["pts"], x[1]["gd"], x[1]["gf"]), reverse=True)
    qualified.extend([t for t, _ in thirds[:8]])

    for t in qualified:
        reached[t] = 1                          # qualifié pour les 32es/16es

    # Bracket à têtes de série : 1 vs N, 2 vs N-1, ... puis élimination directe.
    bracket = list(qualified)
    round_code = 1
    while len(bracket) > 1:
        round_code += 1
        n = len(bracket)
        winners = []
        for i in range(n // 2):
            h, a = bracket[i], bracket[n - 1 - i]
            hg, ag = sampler.sample(h, a, rng)
            if hg == ag:                        # pas de nul en élimination directe
                w = h if rng.random() < 0.5 else a
            else:
                w = h if hg > ag else a
            winners.append(w)
            reached[w] = round_code
        bracket = winners
    return reached


def simulate_world_cup(n_sims: int = 2000, seed: int = 42,
                       sampler: _MatchSampler | None = None,
                       conn=None) -> dict:
    own_conn = conn is None
    conn = conn or db.connect()
    try:
        fixtures = _load_group_fixtures(conn)
    finally:
        if own_conn:
            conn.close()
    if fixtures.empty:
        return {"error": "aucun match de Coupe du Monde dans fixtures"}

    groups = _infer_groups(fixtures)
    sampler = sampler or _MatchSampler()
    rng = np.random.default_rng(seed)

    teams = sorted({t for g in groups for t in g})
    max_round = 1
    counts = {t: defaultdict(int) for t in teams}
    for _ in range(n_sims):
        reached = _simulate_once(groups, sampler, rng)
        for t, r in reached.items():
            max_round = max(max_round, r)
            for tour in range(r + 1):
                counts[t][tour] += 1

    def pct(t, tour):
        return round(counts[t].get(tour, 0) / n_sims, 4)

    table = []
    for t in teams:
        table.append({
            "team": t,
            "p_advance": pct(t, 1),             # sortir des poules
            "p_quarter": pct(t, max_round - 2) if max_round >= 3 else 0.0,
            "p_final": pct(t, max_round - 1) if max_round >= 2 else 0.0,
            "p_title": pct(t, max_round),
        })
    table.sort(key=lambda r: r["p_title"], reverse=True)
    return {"n_sims": n_sims, "n_groups": len(groups),
            "n_teams": len(teams), "rounds": max_round, "teams": table}


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = simulate_world_cup(n_sims=2000)
    res["teams"] = res["teams"][:16]            # top 16 favoris à l'affichage
    print(json.dumps(res, indent=2, ensure_ascii=False))
