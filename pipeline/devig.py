"""Retrait de la marge du bookmaker (« devig ») -> probabilités implicites équitables.

Une cote décimale `o` correspond à une probabilité brute `1/o`. La somme des `1/o`
sur les issues d'un marché dépasse 1 : l'excédent est la **marge** (l'« over-round »,
le vig) que prend l'opérateur. Pour comparer honnêtement nos probabilités à celles
du marché, il faut RETIRER cette marge et obtenir des probabilités qui somment à 1.

Deux méthodes :

  * `proportional` (multiplicative) : on divise chaque `1/o` par la somme.
    Simple, mais répartit la marge à l'identique sur favoris et outsiders.

  * `power` : on cherche l'exposant k tel que `Σ (1/o_i)^k = 1`. Plus réaliste,
    car la marge réelle pèse davantage sur les outsiders (favourite-longshot bias).

Les deux renvoient un vecteur de probabilités équitables (somme = 1) et la marge
mesurée. Utilisé pour le CLV et pour estimer une « vraie » proba de marché.
"""

from __future__ import annotations

import math


def overround(odds: list[float]) -> float:
    """Marge du marché : somme des probabilités brutes (1/o) - 1. 0 = marché parfait."""
    return sum(1.0 / o for o in odds if o and o > 0) - 1.0


def _clean(odds: list[float]) -> list[float] | None:
    vals = [float(o) for o in odds if o and o > 1.0]
    return vals if len(vals) == len(odds) else None


def proportional(odds: list[float]) -> list[float] | None:
    """Devig multiplicatif : p_i = (1/o_i) / Σ(1/o_j). Renvoie None si cotes invalides."""
    vals = _clean(odds)
    if not vals:
        return None
    raw = [1.0 / o for o in vals]
    s = sum(raw)
    return [r / s for r in raw]


def power(odds: list[float], *, tol: float = 1e-10, max_iter: int = 100) -> list[float] | None:
    """Devig par la méthode des puissances : trouve k tel que Σ (1/o_i)^k = 1.

    Résolu par bissection sur k (la somme est strictement décroissante en k pour
    des probabilités brutes < 1). Renvoie None si cotes invalides.
    """
    vals = _clean(odds)
    if not vals:
        return None
    raw = [1.0 / o for o in vals]
    if abs(sum(raw) - 1.0) < tol:
        return list(raw)

    def total(k: float) -> float:
        return sum(r ** k for r in raw)

    lo, hi = 0.0, 1.0
    # garantir l'encadrement : total(0)=n>1 ; total grandit -> on monte hi si besoin
    while total(hi) > 1.0 and hi < 1e6:
        lo, hi = hi, hi * 2.0
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        t = total(mid)
        if abs(t - 1.0) < tol:
            break
        if t > 1.0:
            lo = mid
        else:
            hi = mid
    k = 0.5 * (lo + hi)
    probs = [r ** k for r in raw]
    s = sum(probs)
    return [p / s for p in probs]  # normalisation de sécurité


def fair_probs(odds: list[float], method: str = "power") -> list[float] | None:
    """Probabilités implicites équitables (somme = 1) selon la méthode choisie."""
    if method == "proportional":
        return proportional(odds)
    if method == "power":
        return power(odds)
    raise ValueError(f"méthode de devig inconnue : {method!r}")
