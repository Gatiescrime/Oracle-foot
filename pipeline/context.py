"""Contexte géographique et calendaire (Phase P6).

Deux signaux pré-match dérivés du LIEU et du CALENDRIER, sans aucune fuite :
  - distance de déplacement (haversine entre le lieu du match précédent et le lieu
    du match courant, par équipe) ;
  - congestion du calendrier (nombre de matchs dans une fenêtre glissante récente).

Le lieu d'un match est, pour un match NON neutre, le stade de l'équipe à domicile
(coordonnées versionnées dans `data/venues.csv`). Un match en terrain neutre ou une
équipe absente du fichier -> coordonnées inconnues -> distance NaN (assumé).

Anti-fuite : les coordonnées sont une donnée STRUCTURELLE (où joue le club), jamais
un résultat. La distance/congestion d'un match n'utilisent que le passé de l'équipe
et le lieu du match courant (connu d'avance). Aucune information postérieure.

Alignement des noms : libellés du CSV -> noms canoniques par correspondance exacte
normalisée puis appariement flou (réutilise `names`), comme `squad_value`.
"""

from __future__ import annotations

import csv
import io
import logging
import math

from . import config, names

log = logging.getLogger(__name__)

# Score minimal d'appariement flou pour relier un libellé CSV à un nom canonique.
_MIN_MATCH_SCORE = 0.6

_EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance du grand cercle (km) entre deux points (degrés)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _read_csv_text(text: str) -> dict[str, tuple[float, float]]:
    """Parse le texte CSV -> {libellé brut: (lat, lon)}. Ignore commentaires/vides."""
    out: dict[str, tuple[float, float]] = {}
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return out
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    for row in reader:
        name = (row.get("team") or "").strip()
        lat_s = (row.get("lat") or "").strip()
        lon_s = (row.get("lon") or "").strip()
        if not name or not lat_s or not lon_s:
            continue
        try:
            lat, lon = float(lat_s), float(lon_s)
        except ValueError:
            continue
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            out[name] = (lat, lon)
    return out


def _load_raw_coords() -> dict[str, tuple[float, float]]:
    """Charge {libellé: (lat, lon)} depuis le CSV local (repli propre si absent)."""
    try:
        with open(config.VENUES_CSV, encoding="utf-8") as fh:
            return _read_csv_text(fh.read())
    except FileNotFoundError:
        log.info("context: aucun fichier %s", config.VENUES_CSV)
        return {}


def _align_to_teams(raw: dict[str, tuple[float, float]],
                    teams: dict[str, str]) -> dict[str, tuple[float, float]]:
    """Relie {libellé CSV: coords} -> {team_id: coords} via les noms canoniques.

    Étape 1 : correspondance exacte normalisée. Étape 2 : appariement flou sur les
    libellés restants (seuil `_MIN_MATCH_SCORE`). Même logique que `squad_value`.
    """
    by_norm = {names._norm(n): tid for tid, n in teams.items()}
    result: dict[str, tuple[float, float]] = {}
    unmatched: list[str] = []

    for label, coords in raw.items():
        tid = by_norm.get(names._norm(label))
        if tid is not None:
            result[tid] = coords
        else:
            unmatched.append(label)

    if unmatched:
        canon_items = list(teams.items())  # [(tid, name)]
        for label in unmatched:
            best_tid, best_score = None, 0.0
            for tid, name in canon_items:
                s = names._similarity(label, name)
                if s > best_score:
                    best_tid, best_score = tid, s
            if best_tid is not None and best_score >= _MIN_MATCH_SCORE:
                result.setdefault(best_tid, raw[label])

    return result


def coords_by_team_id(conn, domain: str) -> dict[str, tuple[float, float]]:
    """{team_id: (lat, lon)} pour un domaine, aligné sur la table teams.

    Dict possiblement partiel : équipe absente -> pas de clé (l'appelant produit
    NaN). Sans fichier -> dict vide (repli propre). Les sélections nationales jouent
    souvent en terrain neutre : leur lieu reste typiquement inconnu, c'est assumé.
    """
    raw = _load_raw_coords()
    if not raw:
        return {}
    rows = conn.execute(
        "SELECT team_id, canonical_name FROM teams WHERE domain = ?",
        (domain,),
    ).fetchall()
    teams = {r["team_id"]: r["canonical_name"] for r in rows}
    if not teams:
        return {}
    return _align_to_teams(raw, teams)
