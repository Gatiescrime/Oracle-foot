"""Valeur marchande des effectifs comme proxy de niveau (Phase P5).

Source : un CSV versionné et éditable (`data/squad_values.csv`, colonnes `team` et
`value_meur`), ou une URL optionnelle (`SQUAD_VALUE_URL`) rafraîchie via le cache
disque. La valeur d'effectif (style Transfermarkt) est un proxy robuste du niveau
d'une équipe, indépendant de la forme récente : utile au début de saison ou pour
les promus, là où l'Elo et la forme manquent de recul.

Anti-fuite : la valeur est une caractéristique STRUCTURELLE pré-match (état de
l'effectif), pas un résultat. On l'attache par nom d'équipe, jamais par date/score.
Une équipe absente du fichier donne une valeur NaN (gérée nativement par XGBoost).

Alignement des noms : on apparie les libellés du CSV à nos noms canoniques par
correspondance exacte normalisée puis appariement flou (réutilise `names`).
"""

from __future__ import annotations

import csv
import io
import logging

from . import config, names

log = logging.getLogger(__name__)

# Score minimal d'appariement flou pour relier un libellé CSV à un nom canonique.
_MIN_MATCH_SCORE = 0.6


def _read_csv_text(text: str) -> dict[str, float]:
    """Parse le texte CSV -> {libellé brut: valeur}. Robuste aux commentaires/vides."""
    out: dict[str, float] = {}
    # Filtre les commentaires (#) et lignes vides avant le DictReader.
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return out
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    for row in reader:
        name = (row.get("team") or "").strip()
        raw = (row.get("value_meur") or row.get("value") or "").strip()
        if not name or not raw:
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        if val > 0:
            out[name] = val
    return out


def _load_raw_values() -> dict[str, float]:
    """Charge les paires (libellé, valeur) depuis l'URL (cache) ou le CSV local."""
    if config.SQUAD_VALUE_URL:
        try:
            from . import http
            raw = http.fetch(config.SQUAD_VALUE_URL, ttl_hours=24 * 30)
            vals = _read_csv_text(raw.decode("utf-8", errors="replace"))
            if vals:
                return vals
        except Exception as exc:  # repli silencieux sur le CSV local
            log.warning("squad_value: URL indisponible (%s), repli CSV local", exc)
    try:
        with open(config.SQUAD_VALUE_CSV, encoding="utf-8") as fh:
            return _read_csv_text(fh.read())
    except FileNotFoundError:
        log.info("squad_value: aucun fichier %s", config.SQUAD_VALUE_CSV)
        return {}


def _align_to_teams(raw: dict[str, float],
                    teams: dict[str, str]) -> dict[str, float]:
    """Relie {libellé CSV: valeur} -> {team_id: valeur} via les noms canoniques.

    `teams` = {team_id: canonical_name}. Étape 1 : correspondance exacte normalisée.
    Étape 2 : appariement flou sur les libellés CSV restants, seuil `_MIN_MATCH_SCORE`.
    """
    by_norm = {names._norm(n): tid for tid, n in teams.items()}
    result: dict[str, float] = {}
    unmatched: list[str] = []

    for label, val in raw.items():
        tid = by_norm.get(names._norm(label))
        if tid is not None:
            result[tid] = val
        else:
            unmatched.append(label)

    if unmatched:
        # appariement flou : meilleur canonique au-dessus du seuil, par libellé
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


def values_by_team_id(conn, domain: str) -> dict[str, float]:
    """{team_id: valeur d'effectif (M€)} pour un domaine, aligné sur la table teams.

    Renvoie un dict possiblement partiel ; les équipes absentes -> pas de clé
    (l'appelant produit NaN). Sans fichier ni URL -> dict vide (repli propre).
    """
    raw = _load_raw_values()
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
