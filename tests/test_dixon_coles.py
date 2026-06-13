"""Tests du modèle Dixon-Coles (Étape 2 : convergence + terrain neutre).

On vérifie deux propriétés essentielles, sans réseau ni données réelles :
  - l'estimation CONVERGE même avec beaucoup d'équipes (cas « sélections »),
    grâce au budget d'évaluations suffisant ;
  - l'avantage du terrain est NUL sur terrain neutre (essentiel pour la CdM).
"""

import numpy as np
import pandas as pd

from pipeline.dixon_coles import DixonColesModel, fit_dixon_coles


def _synthetic_matches(n_teams=40, n_matches=3000, seed=0):
    """Championnat synthétique : forces d'attaque/défense connues, buts ~ Poisson."""
    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(n_teams)]
    strength = {t: rng.normal(0, 0.4) for t in teams}
    base, home_adv = 0.2, 0.25
    rows = []
    start = pd.Timestamp("2018-01-01")
    for k in range(n_matches):
        h, a = rng.choice(teams, size=2, replace=False)
        neutral = bool(rng.random() < 0.15)
        ha = 0.0 if neutral else home_adv
        lam = np.exp(base + ha + strength[h] - strength[a] * 0.5)
        mu = np.exp(base + strength[a] - strength[h] * 0.5)
        rows.append({
            "date": start + pd.Timedelta(days=k),
            "home_team": h, "away_team": a,
            "home_goals": int(rng.poisson(lam)),
            "away_goals": int(rng.poisson(mu)),
            "neutral": neutral,
        })
    return pd.DataFrame(rows)


def test_fit_converges_with_many_teams():
    """Avec ~40 équipes (≈370 paramètres), l'optimiseur doit converger pour de bon."""
    df = _synthetic_matches(n_teams=40, n_matches=3000)
    model = fit_dixon_coles(df, verbose=False)
    assert model.converged is True
    assert 0.0 < model.home_adv < 0.6        # avantage terrain plausible, récupéré


def test_neutral_ground_has_no_home_advantage():
    """Sur terrain neutre, home_adv ne doit PAS entrer dans le lambda domicile."""
    m = DixonColesModel(teams=["A", "B"], attack={"A": 0.3, "B": 0.0},
                        defence={"A": 0.0, "B": 0.1}, base=0.2,
                        home_adv=0.25, rho=-0.05, converged=True)
    lam_home, _ = m.predict_lambdas("A", "B", neutral=False)
    lam_neutral, _ = m.predict_lambdas("A", "B", neutral=True)
    # le retrait de home_adv divise lambda par exp(home_adv)
    assert lam_neutral < lam_home
    assert np.isclose(lam_neutral, lam_home / np.exp(m.home_adv))


def test_inactive_teams_treated_as_average():
    """Une équipe quasi absente reste « moyenne » (attaque/défense ~ non définies)."""
    df = _synthetic_matches(n_teams=20, n_matches=1500)
    # ajoute une équipe avec un seul match : sous le seuil min_matches
    df = pd.concat([df, pd.DataFrame([{
        "date": pd.Timestamp("2018-01-01"), "home_team": "RARE", "away_team": "T00",
        "home_goals": 0, "away_goals": 0, "neutral": False}])], ignore_index=True)
    model = fit_dixon_coles(df, verbose=False)
    assert "RARE" not in model.attack          # non paramétrée → traitée comme moyenne
    lam, mu = model.predict_lambdas("RARE", "T00")
    assert lam > 0 and mu > 0                   # prédiction quand même possible
