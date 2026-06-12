"""Tests de la simulation Monte-Carlo (Phase 6).

On n'appelle JAMAIS le vrai modèle : on injecte un _MatchSampler factice piloté par
les forces relatives des équipes. Critères : 12 groupes inférés, probabilités bien
formées (∈ [0,1], décroissantes par tour), et le favori ressort en tête.
"""

import numpy as np
import pandas as pd
import pytest

from pipeline import simulate


def _toy_fixtures():
    """3 groupes de 4 équipes (round-robin intra-groupe)."""
    groups = [["A1", "A2", "A3", "A4"], ["B1", "B2", "B3", "B4"],
              ["C1", "C2", "C3", "C4"]]
    rows = []
    for g in groups:
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                rows.append({"home": g[i], "away": g[j]})
    return pd.DataFrame(rows)


class _StrengthSampler:
    """Score tiré selon une force par équipe : la plus forte marque plus."""

    def __init__(self, strength):
        self.strength = strength

    def sample(self, home, away, rng):
        hg = rng.poisson(self.strength.get(home, 1.0))
        ag = rng.poisson(self.strength.get(away, 1.0))
        return int(hg), int(ag)


def test_infer_groups_count_and_size():
    groups = simulate._infer_groups(_toy_fixtures())
    assert len(groups) == 3
    assert all(len(g) == 4 for g in groups)


def test_rank_group_orders_by_points():
    table = {"X": {"pts": 6, "gd": 3, "gf": 5},
             "Y": {"pts": 6, "gd": 5, "gf": 7},
             "Z": {"pts": 1, "gd": -2, "gf": 1}}
    assert simulate._rank_group(table) == ["Y", "X", "Z"]


def test_simulate_once_returns_reached_rounds():
    groups = simulate._infer_groups(_toy_fixtures())
    strength = {t: 1.0 for g in groups for t in g}
    sampler = _StrengthSampler(strength)
    rng = np.random.default_rng(0)
    reached = simulate._simulate_once(groups, sampler, rng)
    assert set(reached) == {t for g in groups for t in g}
    assert all(r >= 0 for r in reached.values())
    assert max(reached.values()) >= 1


def test_favorite_wins_most_often():
    fixtures = _toy_fixtures()
    groups = simulate._infer_groups(fixtures)
    strength = {t: 1.0 for g in groups for t in g}
    strength["A1"] = 3.0                      # nette équipe la plus forte
    sampler = _StrengthSampler(strength)

    res = simulate.simulate_world_cup(n_sims=300, seed=1, sampler=sampler,
                                      conn=_FakeConn(fixtures))
    top = res["teams"][0]
    assert top["team"] == "A1"
    assert top["p_title"] > 0.2
    for row in res["teams"]:
        for k in ["p_advance", "p_quarter", "p_final", "p_title"]:
            assert 0.0 <= row[k] <= 1.0
        assert row["p_advance"] >= row["p_title"]


class _FakeConn:
    """Mime le minimum utilisé par simulate_world_cup (_load_group_fixtures)."""

    def __init__(self, fixtures):
        self._fixtures = fixtures

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _patch_loader(monkeypatch):
    monkeypatch.setattr(simulate, "_load_group_fixtures",
                        lambda conn: conn._fixtures)
