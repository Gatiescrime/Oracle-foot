"""Modèle Dixon-Coles (1997) : Poisson bivarié avec correction des scores faibles.

Paramétrage (corrigé par rapport au prototype) :

    log(lambda_dom) = base + home_adv * (terrain non neutre) + attaque_dom + defense_ext
    log(mu_ext)     = base + attaque_ext + defense_dom

- `base` : niveau de but moyen (intercept unique).
- `home_adv` : avantage du terrain PUR, appliqué seulement hors terrain neutre.
  Il vaut donc 0 sur terrain neutre (essentiel pour la Coupe du Monde). Comme il
  n'est plus mélangé à des moyennes domicile/extérieur séparées, il ne « s'absorbe »
  plus dans l'intercept (défaut du prototype).
- `attaque` / `defense` par équipe, centrées (somme nulle) pour l'identifiabilité.
- `rho` : correction Dixon-Coles des petits scores (0-0, 1-0, 0-1, 1-1).

Améliorations pour la CONVERGENCE (sélections, ~260 équipes) :
- régularisation L2 sur attaque/défense (rétrécit les paramètres mal contraints),
- restriction aux équipes ACTIVES (assez de matchs récents) ; les autres sont
  traitées comme une équipe « moyenne » (attaque = défense = 0),
- pondération temporelle exp(-xi * âge) réglable,
- plus d'itérations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson


def tau(x, y, lam, mu, rho):
    """Correction Dixon-Coles des 4 scores faibles."""
    x = np.asarray(x)
    y = np.asarray(y)
    out = np.ones_like(np.asarray(lam, dtype=float))
    out = np.where((x == 0) & (y == 0), 1.0 - lam * mu * rho, out)
    out = np.where((x == 0) & (y == 1), 1.0 + lam * rho, out)
    out = np.where((x == 1) & (y == 0), 1.0 + mu * rho, out)
    out = np.where((x == 1) & (y == 1), 1.0 - rho, out)
    return out


@dataclass
class DixonColesModel:
    teams: list[str]
    attack: dict[str, float] = field(default_factory=dict)
    defence: dict[str, float] = field(default_factory=dict)
    base: float = 0.1
    home_adv: float = 0.25
    rho: float = -0.1
    converged: bool = False

    def _ad(self, team):
        return self.attack.get(team, 0.0), self.defence.get(team, 0.0)

    def predict_lambdas(self, home: str, away: str, neutral: bool = False):
        ah, dh = self._ad(home)
        aa, da = self._ad(away)
        ha = 0.0 if neutral else self.home_adv
        lam = float(np.exp(self.base + ha + ah + da))
        mu = float(np.exp(self.base + aa + dh))
        return lam, mu

    def score_matrix(self, home: str, away: str, neutral: bool = False,
                     max_goals: int = 10, lambdas: tuple | None = None):
        lam, mu = lambdas if lambdas is not None else self.predict_lambdas(home, away, neutral)
        i = np.arange(0, max_goals + 1)
        mat = np.outer(poisson.pmf(i, lam), poisson.pmf(i, mu))
        for x in (0, 1):
            for y in (0, 1):
                mat[x, y] *= float(tau(x, y, lam, mu, self.rho))
        mat /= mat.sum()
        return mat

    def predict(self, home, away, neutral=False, max_goals=10, lambdas=None):
        mat = self.score_matrix(home, away, neutral, max_goals, lambdas=lambdas)
        return _matrix_to_prediction(mat, home, away, neutral, lambdas or self.predict_lambdas(home, away, neutral))

    def to_dict(self) -> dict:
        return {
            "type": "dixon_coles",
            "base": self.base, "home_adv": self.home_adv, "rho": self.rho,
            "attack": self.attack, "defence": self.defence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DixonColesModel":
        return cls(teams=list(d["attack"].keys()), attack=d["attack"], defence=d["defence"],
                   base=d["base"], home_adv=d["home_adv"], rho=d["rho"], converged=True)


def _matrix_to_prediction(mat, home, away, neutral, lambdas):
    home_win = float(np.tril(mat, -1).sum())
    draw = float(np.trace(mat))
    away_win = float(np.triu(mat, 1).sum())
    idx = np.unravel_index(np.argmax(mat), mat.shape)
    n = mat.shape[0]
    xx, yy = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    over25 = float(mat[(xx + yy) > 2].sum())
    btts = float(mat[(xx >= 1) & (yy >= 1)].sum())
    return {
        "home": home, "away": away, "neutral": neutral,
        "exp_home_goals": round(lambdas[0], 2), "exp_away_goals": round(lambdas[1], 2),
        "p_home_win": round(home_win, 4), "p_draw": round(draw, 4), "p_away_win": round(away_win, 4),
        "most_likely_score": [int(idx[0]), int(idx[1])], "p_most_likely": round(float(mat[idx]), 4),
        "p_over_2_5": round(over25, 4), "p_btts": round(btts, 4),
    }


def fit_dixon_coles(matches: pd.DataFrame, xi: float = 0.0018, reg: float = 0.05,
                    min_matches: int = 8, max_iter: int = 500, verbose: bool = True,
                    ref_date=None) -> DixonColesModel:
    """Estime le modèle par maximum de vraisemblance pondéré + régularisé.

    matches : colonnes date, home_team, away_team, home_goals, away_goals, neutral.
    xi      : décroissance temporelle (0.0018/j ~ demi-vie 1 an).
    reg     : force de la régularisation L2 sur attaque/défense.
    min_matches : une équipe doit avoir au moins ce nombre de matchs (pondérés ~)
                  pour être paramétrée ; sinon elle est « moyenne » (0).
    """
    df = matches.dropna(subset=["home_goals", "away_goals"]).copy()
    hg = df["home_goals"].to_numpy(dtype=int)
    ag = df["away_goals"].to_numpy(dtype=int)
    neutral = df.get("neutral", pd.Series(0, index=df.index)).fillna(0).to_numpy().astype(float)

    ref = pd.Timestamp(ref_date) if ref_date is not None else df["date"].max()
    age_days = (ref - df["date"]).dt.days.to_numpy().astype(float)
    weights = np.exp(-xi * age_days)

    # Équipes actives : poids cumulé suffisant (≈ nombre de matchs récents).
    wcount: dict[str, float] = {}
    for t, w in zip(df["home_team"], weights):
        wcount[t] = wcount.get(t, 0.0) + w
    for t, w in zip(df["away_team"], weights):
        wcount[t] = wcount.get(t, 0.0) + w
    # seuil : poids cumulé ≥ min_matches (les matchs récents pèsent ~1)
    active = sorted([t for t, c in wcount.items() if c >= min_matches])
    tindex = {t: i for i, t in enumerate(active)}
    n = len(active)

    def idx_of(series):
        return series.map(lambda t: tindex.get(t, -1)).to_numpy()

    hi = idx_of(df["home_team"])
    ai = idx_of(df["away_team"])
    hi_mask = (hi >= 0).astype(float)
    ai_mask = (ai >= 0).astype(float)
    hi_safe = np.clip(hi, 0, max(n - 1, 0))
    ai_safe = np.clip(ai, 0, max(n - 1, 0))

    base0 = np.log(max(0.5 * (hg.mean() + ag.mean()), 0.1))

    # vecteur : [attack(n), defence(n), base, home_adv, rho]
    p_base, p_home, p_rho = 2 * n, 2 * n + 1, 2 * n + 2
    x0 = np.zeros(2 * n + 3)
    x0[p_base] = base0
    x0[p_home] = 0.25
    x0[p_rho] = -0.05

    def unpack(params):
        attack = params[:n]
        defence = params[n:2 * n]
        return attack, defence, params[p_base], params[p_home], params[p_rho]

    def neg_log_lik(params):
        attack, defence, base, home_adv, rho = unpack(params)
        ah = attack[hi_safe] * hi_mask
        dh = defence[hi_safe] * hi_mask
        aa = attack[ai_safe] * ai_mask
        da = defence[ai_safe] * ai_mask

        log_lam = base + home_adv * (1 - neutral) + ah + da
        log_mu = base + aa + dh
        lam = np.clip(np.exp(log_lam), 1e-6, 30)
        mu = np.clip(np.exp(log_mu), 1e-6, 30)

        log_pois = (hg * np.log(lam) - lam) + (ag * np.log(mu) - mu)
        t = np.clip(tau(hg, ag, lam, mu, rho), 1e-9, None)
        ll = np.sum(weights * (log_pois + np.log(t)))

        # Régularisation L2 : rétrécit attaque/défense vers 0. Elle assure aussi
        # l'identifiabilité (parmi les solutions équivalentes base/attaque, la L2
        # sélectionne celle de moyenne ~0), sans pénalité de centrage rigide qui
        # raidissait l'optimisation.
        penalty = reg * (np.sum(attack ** 2) + np.sum(defence ** 2))
        return -ll + penalty

    bounds = [(-3, 3)] * (2 * n) + [(-2, 2), (-0.5, 0.6), (-0.2, 0.2)]
    res = minimize(neg_log_lik, x0, method="L-BFGS-B", bounds=bounds,
                   options={"maxiter": max_iter, "maxfun": max_iter * 50})

    attack_v, defence_v, base, home_adv, rho = unpack(res.x)
    if n:
        attack_v = attack_v - attack_v.mean()
        defence_v = defence_v - defence_v.mean()
    attack = {t: float(attack_v[i]) for t, i in tindex.items()}
    defence = {t: float(defence_v[i]) for t, i in tindex.items()}

    if verbose:
        print(f"  Dixon-Coles : {n} équipes actives / {len(wcount)}, {len(df)} matchs, "
              f"base={base:.3f}, home_adv={home_adv:.3f}, rho={rho:.3f}, convergé={res.success}")

    return DixonColesModel(teams=active, attack=attack, defence=defence,
                           base=float(base), home_adv=float(home_adv), rho=float(rho),
                           converged=bool(res.success))
