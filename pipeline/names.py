"""Correspondance des noms d'équipes entre sources.

Problème : football-data écrit "Man City", understat écrit "Manchester City",
martj42 écrit les sélections autrement encore. On veut un identifiant canonique
unique par équipe, et une table d'alias (source, libellé) -> identifiant.

Stratégie clubs : football-data fait foi (c'est la source des résultats + cotes).
Pour chaque ligue/saison, understat et football-data ont le MÊME ensemble d'équipes
(~20). On apparie les deux listes par un appariement optimal (coût = 1 - similarité),
ce qui évite les confusions du type "Ath Madrid" / "Ath Bilbao" qu'un simple plus
proche voisin commettrait. Quelques cas connus sont forcés via OVERRIDES.

Sélections : martj42 est source unique, ses libellés sont déjà propres -> canoniques.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

import numpy as np
from scipy.optimize import linear_sum_assignment

# Cas où la similarité textuelle seule pourrait se tromper.
# clé = libellé understat (normalisé minuscule), valeur = nom football-data exact.
OVERRIDES = {
    "athletic club": "Ath Bilbao",
    "atletico madrid": "Ath Madrid",
    "real sociedad": "Sociedad",
    "real betis": "Betis",
    "espanyol": "Espanol",
    "rayo vallecano": "Vallecano",
    "celta vigo": "Celta",
    "deportivo la coruna": "La Coruna",
    "manchester city": "Man City",
    "manchester united": "Man United",
    "newcastle united": "Newcastle",
    "nottingham forest": "Nott'm Forest",
    "wolverhampton wanderers": "Wolves",
    "sheffield united": "Sheffield United",
    "paris saint germain": "Paris SG",
    "saint-etienne": "St Etienne",
    "bayer leverkusen": "Leverkusen",
    "borussia dortmund": "Dortmund",
    "borussia m.gladbach": "M'gladbach",
    "bayern munich": "Bayern Munich",
    "hellas verona": "Verona",
    "internazionale": "Inter",
    "ac milan": "Milan",
}


# Variantes connues de NOMS DE SÉLECTIONS d'une source à l'autre (the-odds-api,
# FIFA, bookmakers…). clé = libellé NORMALISÉ (accents/casse/ponctuation retirés,
# espaces compactés) ; valeur = nom canonique de NOTRE base (libellés martj42).
# But : qu'« USA », « Korea Republic », « Czechia », « Côte d'Ivoire »… tombent sur
# le bon pays, dans les deux sens d'appariement. Priorité aux nations de Coupe du
# Monde / grandes sélections, là où l'écart de libellé est fréquent.
NATION_ALIASES = {
    "usa": "United States",
    "united states of america": "United States",
    "korea republic": "South Korea",
    "republic of korea": "South Korea",
    "south korea": "South Korea",
    "korea dpr": "North Korea",
    "dpr korea": "North Korea",
    "ir iran": "Iran",
    "iran islamic republic of": "Iran",
    "czechia": "Czech Republic",
    "cote d ivoire": "Ivory Coast",
    "ivory coast": "Ivory Coast",
    "turkiye": "Turkey",
    "china pr": "China",
    "chinese taipei": "Taiwan",
    "congo dr": "DR Congo",
    "dr congo": "DR Congo",
    "democratic republic of congo": "DR Congo",
    "ireland": "Republic of Ireland",
    "republic of ireland": "Republic of Ireland",
    "bosnia herzegovina": "Bosnia and Herzegovina",
    "bosnia and herzegovina": "Bosnia and Herzegovina",
    "cabo verde": "Cape Verde",
    "cape verde islands": "Cape Verde",
}


def slugify(name: str) -> str:
    """Identifiant canonique stable et lisible (sans accents, en minuscules)."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def resolve_alias(label: str) -> str:
    """Nom canonique connu pour `label`, robuste aux variantes de sources.

    - Sélections : table `NATION_ALIASES` (USA→United States, Czechia→Czech Republic…).
    - Clubs : `OVERRIDES` (libellé long understat → nom court football-data).
    Sinon, renvoie `label` inchangé.
    """
    key = " ".join(_norm(label).split())          # normalisé + espaces compactés
    if key in NATION_ALIASES:
        return NATION_ALIASES[key]
    if key in OVERRIDES:
        return OVERRIDES[key]
    return label


def alias_key(label: str) -> str:
    """Clé de comparaison stable et tolérante (accents, casse, alias connus).

    Deux libellés qui désignent la même équipe (« USA » / « United States »,
    « Manchester City » / « Man City ») produisent la MÊME clé.
    """
    return slugify(resolve_alias(label))


def _norm(name: str) -> str:
    """Normalisation pour comparer deux libellés (accents, casse, ponctuation)."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def _similarity(a: str, b: str) -> float:
    """Score 0..1 mêlant similarité de séquence et recouvrement de tokens."""
    na, nb = _norm(a), _norm(b)
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    tok = len(ta & tb) / max(1, len(ta | tb))
    # bonus si l'un est préfixe/sous-ensemble de l'autre (Man / Manchester)
    pref = 0.2 if (na in nb or nb in na) else 0.0
    return min(1.0, 0.5 * seq + 0.4 * tok + pref + 0.1 * tok)


def match_sets(source_names: list[str], canonical_names: list[str]) -> dict[str, str]:
    """Apparie chaque nom de `source_names` à un nom de `canonical_names`.

    Applique d'abord les OVERRIDES (contraintes dures), puis un appariement optimal
    sur les restants. Renvoie {source_name -> canonical_name}.
    """
    mapping: dict[str, str] = {}
    remaining_src = list(source_names)
    remaining_can = list(canonical_names)

    # 1) overrides + correspondances exactes (après normalisation)
    canon_by_norm = {_norm(c): c for c in canonical_names}
    for s in list(remaining_src):
        ns = _norm(s)
        target = None
        if ns in OVERRIDES and OVERRIDES[ns] in remaining_can:
            target = OVERRIDES[ns]
        elif ns in canon_by_norm and canon_by_norm[ns] in remaining_can:
            target = canon_by_norm[ns]
        if target is not None:
            mapping[s] = target
            remaining_src.remove(s)
            remaining_can.remove(target)

    # 2) appariement optimal sur le reste (Hungarian sur le coût = 1 - similarité)
    if remaining_src and remaining_can:
        cost = np.zeros((len(remaining_src), len(remaining_can)))
        for i, s in enumerate(remaining_src):
            for j, c in enumerate(remaining_can):
                cost[i, j] = 1.0 - _similarity(s, c)
        rows, cols = linear_sum_assignment(cost)
        for i, j in zip(rows, cols):
            mapping[remaining_src[i]] = remaining_can[j]

    return mapping
