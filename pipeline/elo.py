"""
Moteur Elo pour le football (méthode World Football Elo Ratings).

Le rating Elo est la feature unique la plus prédictive (cf. littérature citée
dans le brief). On le calcule nous-mêmes depuis l'historique des résultats :
plus robuste et reproductible qu'un scraping.

Spécificités football vs échecs :
  - facteur K modulé par l'importance du match (amical < qualif < CdM)
  - bonus pour l'ampleur du score (un 4-0 déplace plus le rating qu'un 1-0)
  - avantage du terrain pris en compte dans le résultat attendu

On expose, pour chaque match, l'Elo des deux équipes JUSTE AVANT le coup d'envoi
(pas de fuite de données), puis on met à jour après.
"""

from __future__ import annotations

import math

import pandas as pd

from . import config

# Importance du match -> facteur K de base (méthode eloratings.net adaptée)
TOURNAMENT_WEIGHTS = {
    "FIFA World Cup": 60,
    "Copa America": 50,
    "UEFA Euro": 50,
    "African Cup of Nations": 50,
    "AFC Asian Cup": 50,
    "Gold Cup": 50,
    "Confederations Cup": 45,
    "UEFA Nations League": 40,
    "FIFA World Cup qualification": 40,
    "UEFA Euro qualification": 38,
    "Copa America qualification": 38,
    "African Cup of Nations qualification": 38,
    "AFC Asian Cup qualification": 38,
    "Friendly": 20,
}
DEFAULT_WEIGHT = 30  # autres compétitions
CLUB_K = 20          # facteur K fixe pour les ligues de clubs
HOME_ADVANTAGE = 65  # points Elo ajoutés à l'équipe à domicile (modèle historique)
INITIAL_RATING = 1500.0

# --- Phase P5 : avantage du terrain SPÉCIFIQUE à la compétition ------------
# Le poids du terrain varie : très fort en qualifs (voyages, altitude, ambiance),
# plus modéré dans certains championnats, NUL en terrain neutre. Valeurs en points
# Elo, fixées (pas apprises sur les résultats -> aucune fuite). Utilisées à la place
# du HOME_ADVANTAGE constant quand `config.P5_FEATURES_ENABLED` est vrai.
HOME_ADVANTAGE_BY_COMP = {
    # clubs (par championnat)
    "Premier League": 60, "La Liga": 70, "Serie A": 65,
    "Bundesliga": 60, "Ligue 1": 65,
    # sélections
    "FIFA World Cup qualification": 80, "UEFA Euro qualification": 75,
    "UEFA Nations League": 70, "FIFA World Cup": 55, "UEFA Euro": 55,
    "Copa America": 60, "African Cup of Nations": 70, "AFC Asian Cup": 65,
    "Friendly": 40,
}
_DEFAULT_HA_CLUB = 62
_DEFAULT_HA_INTL = 65


def home_advantage_for(competition: str, is_club: bool, neutral: bool = False) -> float:
    """Avantage du terrain (points Elo) pour ce match : 0 si terrain neutre."""
    if neutral:
        return 0.0
    default = _DEFAULT_HA_CLUB if is_club else _DEFAULT_HA_INTL
    return float(HOME_ADVANTAGE_BY_COMP.get(competition, default))


def _match_home_adv(competition: str, is_club: bool, neutral: bool) -> float:
    """HA effectif pour le calcul Elo : par compétition (P5) ou constant (historique)."""
    if neutral:
        return 0.0
    if config.P5_FEATURES_ENABLED:
        return home_advantage_for(competition, is_club, neutral=False)
    return float(HOME_ADVANTAGE)


# --- Elo offensif / défensif (Phase P5) ------------------------------------
# Au lieu d'un rating unique « niveau global », on suit DEUX ratings par équipe :
#   - OFFENSIF : capacité à marquer (vs la défense adverse) ;
#   - DÉFENSIF : capacité à ne pas encaisser (vs l'attaque adverse).
# Modèle de buts à lien log (gradient en ligne, type forces d'attaque/défense de
# Dixon-Coles, mais mis à jour match par match). Sépare les équipes « spectacle »
# (forte attaque, faible défense) des équipes « solides » qu'un Elo unique confond.
OFFDEF_K = 6.0          # pas d'apprentissage (faible -> stable)
OFFDEF_GOAL_AVG = 1.40  # buts moyens par équipe et par match (échelle)
OFFDEF_SCALE = 200.0    # points Elo par unité de log-but
OFFDEF_LAMBDA_CLAMP = (0.05, 8.0)


def _offdef_lambda(att: float, dfn: float, ha: float) -> float:
    """Buts attendus = moyenne × exp((attaque − défense_adverse + terrain)/échelle)."""
    z = (att - dfn + ha) / OFFDEF_SCALE
    lam = OFFDEF_GOAL_AVG * math.exp(max(-3.0, min(3.0, z)))
    lo, hi = OFFDEF_LAMBDA_CLAMP
    return max(lo, min(hi, lam))


def _k_factor(competition: str, goal_diff: int, is_club: bool) -> float:
    """Facteur K modulé par l'importance et l'ampleur du score."""
    base = CLUB_K if is_club else TOURNAMENT_WEIGHTS.get(competition, DEFAULT_WEIGHT)
    g = abs(goal_diff)
    if g <= 1:
        mult = 1.0
    elif g == 2:
        mult = 1.5
    else:
        mult = (11 + g) / 8.0  # 3 buts -> 1.75, 4 -> 1.875, etc.
    return base * mult


def _expected(rating_a: float, rating_b: float) -> float:
    """Probabilité de gain attendue de A contre B (logistique Elo)."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def compute_elo(matches: pd.DataFrame, is_club: bool) -> pd.DataFrame:
    """Ajoute home_elo / away_elo (avant match) au DataFrame et renvoie une copie.

    Renvoie aussi un attribut .attrs['final_elo'] : dict team -> dernier rating.
    """
    # Tri STABLE (mergesort) : préserve l'ordre d'entrée pour les matchs de même
    # date, donc le calcul est déterministe et reproductible (sinon l'ordre intra-
    # journée varie d'un run à l'autre).
    df = matches.sort_values("date", kind="mergesort").reset_index(drop=True).copy()
    ratings: dict[str, float] = {}

    home_elos, away_elos = [], []
    for row in df.itertuples(index=False):
        ra = ratings.get(row.home_team, INITIAL_RATING)
        rb = ratings.get(row.away_team, INITIAL_RATING)
        home_elos.append(ra)
        away_elos.append(rb)

        # Avantage du terrain : par compétition (P5) ou constant, nul si neutre.
        ha = _match_home_adv(row.competition, is_club, getattr(row, "neutral", False))
        exp_home = _expected(ra + ha, rb)

        gd = row.home_goals - row.away_goals
        if gd > 0:
            score_home = 1.0
        elif gd < 0:
            score_home = 0.0
        else:
            score_home = 0.5

        k = _k_factor(row.competition, gd, is_club)
        delta = k * (score_home - exp_home)
        ratings[row.home_team] = ra + delta
        ratings[row.away_team] = rb - delta

    df["home_elo"] = home_elos
    df["away_elo"] = away_elos
    df.attrs["final_elo"] = ratings
    return df


def latest_ratings(matches: pd.DataFrame, is_club: bool) -> dict[str, float]:
    return compute_elo(matches, is_club).attrs["final_elo"]


def compute_elo_offdef(matches: pd.DataFrame, is_club: bool) -> pd.DataFrame:
    """Ajoute les ratings OFFENSIF/DÉFENSIF (avant match) des deux équipes.

    Colonnes produites : home_off_elo, home_def_elo, away_off_elo, away_def_elo
    (toutes lues AVANT le coup d'envoi -> sans fuite). Attribut .attrs['final_offdef']
    : dict team -> {"off": ..., "def": ...} (état après le dernier match).

    Indépendant de `P5_FEATURES_ENABLED` : ces colonnes sont toujours calculées de
    façon déterministe ; elles ne servent au modèle que si le drapeau est actif.
    """
    df = matches.sort_values("date", kind="mergesort").reset_index(drop=True).copy()
    off: dict[str, float] = {}
    dfn: dict[str, float] = {}

    h_off, h_def, a_off, a_def = [], [], [], []
    for row in df.itertuples(index=False):
        oh = off.get(row.home_team, INITIAL_RATING)
        dh = dfn.get(row.home_team, INITIAL_RATING)
        oa = off.get(row.away_team, INITIAL_RATING)
        da = dfn.get(row.away_team, INITIAL_RATING)
        h_off.append(oh); h_def.append(dh); a_off.append(oa); a_def.append(da)

        ha = home_advantage_for(row.competition, is_club, getattr(row, "neutral", False))
        lam_home = _offdef_lambda(oh, da, ha)    # attaque domicile vs défense extérieur
        lam_away = _offdef_lambda(oa, dh, -ha)   # attaque extérieur vs défense domicile

        err_home = row.home_goals - lam_home
        err_away = row.away_goals - lam_away

        # L'attaque monte si on marque plus que prévu ; la défense adverse baisse d'autant.
        off[row.home_team] = oh + OFFDEF_K * err_home
        dfn[row.away_team] = da - OFFDEF_K * err_home
        off[row.away_team] = oa + OFFDEF_K * err_away
        dfn[row.home_team] = dh - OFFDEF_K * err_away

    df["home_off_elo"] = h_off
    df["home_def_elo"] = h_def
    df["away_off_elo"] = a_off
    df["away_def_elo"] = a_def
    df.attrs["final_offdef"] = {
        t: {"off": off.get(t, INITIAL_RATING), "def": dfn.get(t, INITIAL_RATING)}
        for t in set(off) | set(dfn)
    }
    return df
