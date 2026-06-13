"""Script HORS LIGNE (one-shot) : génère `pipeline/club_crests.py`.

But : associer à chaque club de notre base un écusson (URL d'image stable et
gratuite). On interroge TheSportsDB (clé de démo « 3 ») une seule fois, on
désambiguïse par PAYS (England/Spain/Germany/Italy/France) pour ne jamais
afficher l'écusson d'un homonyme, puis on fige le résultat dans un module Python.

Le runtime (pipeline/badges.py) ne fait alors AUCUN appel réseau : il lit la table
figée. Un club non résolu retombe proprement sur la pastille d'initiales.

Usage : `.venv/bin/python -m scripts.build_club_crests`
"""

from __future__ import annotations

import json
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

from pipeline import db
import pandas as pd

API = "https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t="

LEAGUE_COUNTRY = {
    "Premier League": "England", "La Liga": "Spain", "Bundesliga": "Germany",
    "Serie A": "Italy", "Ligue 1": "France",
}

# Libellés courts football-data -> terme de recherche TheSportsDB (sinon le nom tel quel).
# On vise le NOM COMPLET du club SENIOR pour que la pertinence de l'API renvoie le bon
# club en tête (et jamais une réserve / équipe féminine / amateurs homonymes).
SEARCH_ALIASES = {
    "Ath Bilbao": "Athletic Bilbao", "Ath Madrid": "Atletico Madrid",
    "M'gladbach": "Borussia Monchengladbach", "Nott'm Forest": "Nottingham Forest",
    "Ein Frankfurt": "Eintracht Frankfurt", "Sociedad": "Real Sociedad",
    "Betis": "Real Betis", "Espanol": "Espanyol", "Vallecano": "Rayo Vallecano",
    "Celta": "Celta Vigo", "FC Koln": "1. FC Koln", "Paris SG": "Paris Saint Germain",
    "St Etienne": "Saint Etienne", "Inter": "Inter Milan", "Milan": "AC Milan",
    "Verona": "Hellas Verona", "Man City": "Manchester City",
    "Man United": "Manchester United", "Wolves": "Wolverhampton Wanderers",
    "Hertha": "Hertha BSC", "Mainz": "1. FSV Mainz 05", "Bochum": "VfL Bochum",
    "Hamburg": "Hamburg SV", "West Ham": "West Ham United",
    "Bayern Munich": "Bayern Munich", "RB Leipzig": "RB Leipzig",
    "Sheffield United": "Sheffield United", "Le Havre": "Le Havre",
    # noms SENIOR complets pour écarter les homonymes amateurs/féminins
    "Brighton": "Brighton & Hove Albion", "Dortmund": "Borussia Dortmund",
    "Newcastle": "Newcastle United", "Reims": "Stade de Reims",
    "Monaco": "AS Monaco", "Alaves": "Deportivo Alaves", "Almeria": "UD Almeria",
    "Cadiz": "Cadiz CF", "Leganes": "CD Leganes",
}

# Pays TheSportsDB différent de la ligue (ex. l'AS Monaco joue en L1 mais est monégasque).
COUNTRY_OVERRIDE = {"Monaco": None}  # None = on ne filtre pas par pays (alias + token suffisent)

# Tokens qui trahissent une équipe NON pertinente (féminines, jeunes, réserves, amateurs).
NOISE = {"women", "womens", "ladies", "fem", "feminine", "wfc", "youth",
         "academy", "reserves", "reserve", "casuals", "amateurs", "amateur",
         "u21", "u23", "u19", "u18", "u17", "u20", "ii", "b", "dev"}


def _clubs() -> dict[str, str]:
    conn = db.connect()
    q = """SELECT t.canonical_name AS name, m.competition AS comp, COUNT(*) c
           FROM teams t JOIN matches m
             ON (m.home_team_id=t.team_id OR m.away_team_id=t.team_id)
           WHERE t.domain='club' GROUP BY t.canonical_name, m.competition"""
    df = pd.read_sql_query(q, conn)
    conn.close()
    return df.sort_values("c").groupby("name").tail(1).set_index("name")["comp"].to_dict()


def _search(term: str) -> list[dict]:
    url = API + urllib.parse.quote(term)
    with urllib.request.urlopen(url, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data.get("teams") or []


def _tokens(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))  # retire les accents
    return {w for w in "".join(c if c.isalnum() else " " for c in s.lower()).split() if w}


def _valid(cand: dict, search_tokens: set[str], country: str | None) -> bool:
    """Le candidat est-il le bon club SENIOR (bon sport/pays, sans bruit, nom proche) ?"""
    if cand.get("strSport") != "Soccer":
        return False
    if country is not None and cand.get("strCountry") != country:
        return False
    if not (cand.get("strBadge") or cand.get("strTeamBadge")):
        return False
    ct = _tokens(cand.get("strTeam", ""))
    if ct & NOISE:                       # féminines / jeunes / réserves / amateurs
        return False
    return bool(ct & search_tokens)      # partage au moins un token distinctif


def _resolve_one(name: str, country: str | None) -> tuple[str | None, str | None]:
    term = SEARCH_ALIASES.get(name, name)
    st = _tokens(term)
    for attempt in range(2):
        try:
            teams = _search(term)
        except Exception as e:  # noqa: BLE001
            print(f"  ! réseau {name}: {e}")
            teams = []
        # garde l'ordre de PERTINENCE de l'API : premier candidat valide = le bon.
        for t in teams:
            if _valid(t, st, country):
                return (t.get("strBadge") or t.get("strTeamBadge"), t.get("strTeam"))
        if attempt == 0:
            time.sleep(12.0)             # peut-être bridé : on souffle puis on retente
    return (None, None)


def _existing() -> dict[str, str]:
    try:
        from pipeline.club_crests import CLUB_CRESTS
        return dict(CLUB_CRESTS)
    except Exception:  # noqa: BLE001
        return {}


def main(targets: list[str] | None = None) -> None:
    """Sans argument : résout TOUS les clubs (écrase la table).
    Avec des noms en argument : ne (re)résout QUE ceux-là et fusionne avec l'existant
    (utile pour corriger les manquants/erronés sans tout relancer)."""
    clubs = _clubs()
    out = _existing() if targets else {}
    todo = {n: clubs[n] for n in (targets or clubs) if n in clubs}
    misses: list[str] = []
    for name, comp in sorted(todo.items()):
        country = (COUNTRY_OVERRIDE[name] if name in COUNTRY_OVERRIDE
                   else LEAGUE_COUNTRY.get(comp))
        url, label = _resolve_one(name, country)
        if url:
            out[name] = url
            print(f"  ok {name:18s} -> {label}")
        else:
            misses.append(name)
            print(f"  -- {name:18s} (non résolu, {country})")
        time.sleep(2.5)                  # politesse / anti-bridage de l'API de démo

    print(f"\n{len(out)}/{len(clubs)} résolus au total ; {len(misses)} non résolus "
          f"ce tour : {misses}")
    _write(out)


def _write(mapping: dict[str, str]) -> None:
    lines = [
        '"""Écussons de clubs (URL d\'image) — TABLE FIGÉE, générée hors ligne.',
        "",
        "Généré par `scripts/build_club_crests.py` depuis TheSportsDB. Ne PAS éditer à",
        "la main : relancer le script pour régénérer. Un club absent d'ici retombe sur",
        "la pastille d'initiales (aucun appel réseau au runtime).",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "CLUB_CRESTS: dict[str, str] = {",
    ]
    for k in sorted(mapping):
        lines.append(f"    {k!r}: {mapping[k]!r},")
    lines.append("}")
    path = "pipeline/club_crests.py"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"écrit -> {path} ({len(mapping)} clubs)")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
