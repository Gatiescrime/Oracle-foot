"""Modèle de buteurs « anytime scorer » (Phase 9).

Principe, sans fuite et sans fausse précision :

1. Le modèle de match fournit le nombre de buts attendus de l'équipe (son lambda).
2. On répartit ce lambda entre les joueurs selon leur PART RÉCENTE de production
   (mélange buts marqués + xG, ramené à 90 min) PONDÉRÉE par les minutes attendues.
3. Buts attendus du joueur  g_i = lambda_équipe × poids_i / Σ poids.
4. Probabilité qu'il marque AU MOINS une fois : 1 − exp(−g_i)  (Poisson).

Garde-fous :
- Les minutes attendues = minutes/match de la saison, plafonnées à 90 : un remplaçant
  pèse naturellement moins qu'un titulaire.
- Un facteur de fiabilité (∝ minutes jouées) empêche un joueur à tout petit échantillon
  (1 match, 1 but) d'exploser le classement.
- Lien avec la couche actu : un joueur signalé absent/blessé est EXCLU (0 minute).
- Sélections : aucune donnée joueur fiable -> on renvoie « indisponible », jamais une
  liste inventée.
"""

from __future__ import annotations

import math

# Mélange buts réels / xG pour estimer le taux « vrai » (réduit le bruit).
_GOALS_WEIGHT = 0.5
# Plein crédit de fiabilité au-delà de ce volume de minutes sur la saison (~5 matchs).
_RELIABILITY_FULL_MIN = 450.0
# Minutes attendues maximales (un match complet).
_MAX_MINUTES = 90.0


def _slug(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _per90(goals: float, xg: float, minutes: float) -> float:
    """Taux de buts pour 90 min : mélange buts marqués et xG."""
    if minutes <= 0:
        return 0.0
    blend = _GOALS_WEIGHT * goals + (1 - _GOALS_WEIGHT) * xg
    return blend / minutes * 90.0


def distribute(players: list[dict], lam_team: float, *,
               exclude: set[str] | None = None, top_n: int = 8) -> list[dict]:
    """Répartit `lam_team` buts attendus entre les joueurs et calcule P(marque ≥ 1).

    `players` : liste de dicts {player_name, goals, xg, minutes, games, position}.
    `exclude` : noms (slugifiés en interne) à écarter (joueurs absents/blessés).
    Renvoie les `top_n` joueurs triés par probabilité décroissante.
    """
    exclude_slugs = {_slug(n) for n in (exclude or set())}
    enriched = []
    total_weight = 0.0
    for p in players:
        minutes = float(p.get("minutes") or 0.0)
        games = float(p.get("games") or 0.0)
        if minutes <= 0 or games <= 0:
            continue
        if _slug(p["player_name"]) in exclude_slugs:
            continue
        rate = _per90(float(p.get("goals") or 0.0), float(p.get("xg") or 0.0), minutes)
        exp_minutes = min(minutes / games, _MAX_MINUTES)
        reliability = min(1.0, minutes / _RELIABILITY_FULL_MIN)
        weight = rate * (exp_minutes / 90.0) * reliability
        if weight <= 0:
            continue
        enriched.append({"p": p, "rate": rate, "weight": weight})
        total_weight += weight

    if total_weight <= 0:
        return []

    out = []
    for e in enriched:
        g_i = lam_team * e["weight"] / total_weight
        prob = 1.0 - math.exp(-g_i)
        p = e["p"]
        out.append({
            "name": p["player_name"],
            "position": p.get("position") or "",
            "prob": round(prob, 4),
            "exp_goals": round(g_i, 3),
            "per90": round(e["rate"], 3),
            "minutes": int(p.get("minutes") or 0),
            "goals": int(p.get("goals") or 0),
        })
    out.sort(key=lambda d: d["prob"], reverse=True)
    return out[:top_n]
