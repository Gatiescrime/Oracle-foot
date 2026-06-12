"""Phase P3 — transformer une probabilité + un prix en RECOMMANDATION de mise.

À partir de la probabilité du modèle pour une issue et de la **cote décimale** que
l'utilisateur voit chez son bookmaker, on calcule :
  * l'edge (avantage) : `proba × cote − 1` ;
  * l'espérance de gain par unité misée (commission éventuelle déduite) ;
  * la « value » oui/non (edge au-dessus d'un seuil) ;
  * la mise conseillée en **Kelly fractionné** (prudent), **plafonnée** à une
    fraction du capital (garde-fou anti-ruine).

Principes non négociables :
  * S'il n'y a pas de value, on le DIT clairement et on conseille de NE PAS parier.
  * Aucune promesse de gain. Des garde-fous accompagnent toujours la réponse.
  * La mise est bornée : jamais plus que le plafond, jamais négative.
"""

from __future__ import annotations

from . import config

_WARNINGS = [
    "Probabilités indicatives, à but informatif : aucune garantie de gain.",
    "Ne misez que ce que vous pouvez vous permettre de perdre.",
    "Forte variance : même un pari à valeur positive peut perdre, souvent plusieurs fois de suite.",
]


def kelly_fraction(prob: float, odds: float) -> float:
    """Fraction de Kelly pleine : f* = (b·p − q)/b, b = cote − 1. Bornée à [0,1]."""
    b = odds - 1.0
    if b <= 0 or not (0.0 < prob < 1.0):
        return 0.0
    f = (b * prob - (1.0 - prob)) / b
    return max(0.0, min(1.0, f))


def recommend(prob: float, odds: float, bankroll: float, *,
              edge_threshold: float | None = None,
              kelly_frac: float | None = None,
              max_stake_frac: float | None = None,
              commission: float | None = None) -> dict:
    """Recommandation de mise pour une issue (proba modèle) à une cote donnée.

    Renvoie un dict prêt pour l'API/UI : edge, espérance, value, mise conseillée
    (montant et % du capital), message clair et garde-fous.
    """
    edge_threshold = config.BET_EDGE_THRESHOLD if edge_threshold is None else edge_threshold
    kelly_frac = config.BET_KELLY_FRACTION if kelly_frac is None else kelly_frac
    max_stake_frac = config.BET_MAX_STAKE_FRAC if max_stake_frac is None else max_stake_frac
    commission = config.BET_COMMISSION if commission is None else commission

    valid = odds and odds > 1.0 and 0.0 < prob < 1.0 and bankroll > 0
    if not valid:
        return {
            "valid": False, "value": False, "edge": None,
            "stake_amount": 0.0, "stake_fraction": 0.0,
            "message": "Entrée invalide (cote, probabilité ou capital).",
            "warnings": list(_WARNINGS),
        }

    edge = prob * odds - 1.0
    # Espérance de gain par unité misée (commission prélevée sur le gain net).
    ev_per_unit = prob * (odds - 1.0) * (1.0 - commission) - (1.0 - prob)
    market_implied = 1.0 / odds            # proba brute du bookmaker (avec marge)
    value = edge > edge_threshold

    f_full = kelly_fraction(prob, odds)
    stake_fraction = 0.0
    if value and f_full > 0:
        stake_fraction = min(kelly_frac * f_full, max_stake_frac)  # plafonné
    stake_amount = round(bankroll * stake_fraction, 2)

    if not value:
        message = ("Pas de value à ce prix : la cote n'offre pas d'avantage "
                   "suffisant. Il est déconseillé de parier.")
    elif stake_amount <= 0:
        message = "Avantage trop faible pour justifier une mise."
    else:
        pct = round(stake_fraction * 100.0, 2)
        message = (f"Value détectée. Mise conseillée : {stake_amount} "
                   f"({pct} % du capital), en Kelly fractionné plafonné.")

    return {
        "valid": True,
        "odds": round(float(odds), 3),
        "model_prob": round(float(prob), 4),
        "market_implied_prob": round(float(market_implied), 4),
        "edge": round(float(edge), 4),
        "ev_per_unit": round(float(ev_per_unit), 4),
        "value": bool(value),
        "kelly_full": round(float(f_full), 4),
        "kelly_fraction_used": float(kelly_frac),
        "stake_fraction": round(float(stake_fraction), 4),
        "stake_amount": float(stake_amount),
        "bankroll": float(bankroll),
        "capped_at_fraction": float(max_stake_frac),
        "message": message,
        "warnings": list(_WARNINGS),
    }
