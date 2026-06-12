"""Phase P4 — agrégation de cotes multi-bookmakers en live (line shopping).

On interroge the-odds-api.com pour récupérer, par match à venir, les cotes de
DIZAINES de bookmakers d'un coup. On en extrait le **meilleur prix par marché**
(le « line shopping » : à proba égale, mieux vaut une cote de 2,10 que de 1,95),
puis on croise avec la proba du modèle pour signaler la value.

Principes :
  * La clé API vit dans `.env` (jamais dans le frontend, jamais dans le dépôt).
  * Tout passe par le cache disque (`http.fetch`, TTL configurable) pour préserver
    le quota d'appels du palier gratuit.
  * Sans clé : ce module se signale « non configuré » et l'appelant retombe
    proprement sur les cotes football-data déjà stockées.

Le parsing ne dépend QUE de la forme documentée de l'API :
  event = {home_team, away_team, commence_time, bookmakers:[
      {key, title, markets:[{key:'h2h'|'totals', outcomes:[{name, price, point?}]}]}]}
"""

from __future__ import annotations

import json
import logging

from . import config, http, names

log = logging.getLogger("pipeline.odds_api")

# Compétition canonique (base) -> clé « sport » de the-odds-api.
SPORT_KEYS = {
    "Premier League": "soccer_epl",
    "La Liga": "soccer_spain_la_liga",
    "Bundesliga": "soccer_germany_bundesliga",
    "Serie A": "soccer_italy_serie_a",
    "Ligue 1": "soccer_france_ligue_one",
    "FIFA World Cup": "soccer_fifa_world_cup",
    "UEFA Euro": "soccer_uefa_european_championship",
    "UEFA Nations League": "soccer_uefa_nations_league",
    "FIFA World Cup qualification": "soccer_fifa_world_cup_qualifiers_europe",
}

# Sélections dont le libellé the-odds-api diffère de notre nom canonique.
# (Le reste est résolu par appariement flou ; on ne force que les pièges.)
TEAM_OVERRIDES = {
    "usa": "United States",
    "south korea": "South Korea",
    "north korea": "North Korea",
    "ivory coast": "Ivory Coast",
    "czech republic": "Czech Republic",
    "turkiye": "Turkey",
    "türkiye": "Turkey",
}

# Dernier quota connu (rempli à chaque appel réseau réussi) : affiché en UI.
_LAST_QUOTA: dict = {"remaining": None, "used": None}


def configured() -> bool:
    """Vrai si une clé API est présente (sinon l'appelant fera un repli propre)."""
    return bool(config.ODDS_API_KEY)


def sport_key_for(competition: str) -> str | None:
    return SPORT_KEYS.get(competition)


def last_quota() -> dict:
    return dict(_LAST_QUOTA)


# ---------------------------------------------------------------------------
# Récupération (avec cache disque pour préserver le quota).
# ---------------------------------------------------------------------------
def fetch_odds(sport_key: str, *, use_cache: bool = True,
               ttl_hours: float | None = None) -> list[dict]:
    """Cotes live d'un « sport » (toutes les rencontres à venir couvertes).

    La clé API n'entre PAS dans la clé de cache (le fichier local ne doit pas
    dépendre du secret) : on cache par (sport, régions, marchés).
    """
    if not configured():
        return []
    ttl = config.ODDS_API_TTL_HOURS if ttl_hours is None else ttl_hours
    url = (f"{config.ODDS_API_BASE_URL}/sports/{sport_key}/odds/"
           f"?apiKey={config.ODDS_API_KEY}"
           f"&regions={config.ODDS_API_REGIONS}"
           f"&markets={config.ODDS_API_MARKETS}"
           f"&oddsFormat=decimal")
    cache_key = f"oddsapi:{sport_key}:{config.ODDS_API_REGIONS}:{config.ODDS_API_MARKETS}"

    def _grab_quota(h):
        rem, used = h.get("x-requests-remaining"), h.get("x-requests-used")
        if rem is not None:
            _LAST_QUOTA["remaining"] = int(rem)
        if used is not None:
            _LAST_QUOTA["used"] = int(used)

    try:
        raw = http.fetch(url, cache_key=cache_key, ttl_hours=ttl,
                         use_cache=use_cache, on_headers=_grab_quota)
    except Exception as e:  # noqa: BLE001
        log.warning("the-odds-api indisponible (%s) : %s", sport_key, e)
        return []
    return parse_events(raw)


def parse_events(raw: bytes | str) -> list[dict]:
    """Décode la réponse JSON en liste d'événements (robuste aux erreurs)."""
    try:
        data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (ValueError, AttributeError):
        return []
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Meilleur prix par marché (le cœur du « line shopping »).
# ---------------------------------------------------------------------------
def best_prices(event: dict) -> dict:
    """Meilleur prix (cote la plus haute) + book, pour chaque issue d'un match.

    Renvoie {'h2h': {'home': {odds, book}, 'draw': ..., 'away': ...},
             'totals': {'over': {odds, book, point}, 'under': ...},
             'n_books': int}.
    Les noms d'issue h2h ('home'/'away') sont déduits de event.home_team/away_team.
    """
    home_name = (event.get("home_team") or "").strip()
    away_name = (event.get("away_team") or "").strip()
    h2h: dict[str, dict] = {}
    totals: dict[str, dict] = {}
    books = event.get("bookmakers") or []

    def _better(store: dict, key: str, price: float, book: str, point=None):
        if price is None or price <= 1.0:
            return
        cur = store.get(key)
        if cur is None or price > cur["odds"]:
            store[key] = {"odds": float(price), "book": book}
            if point is not None:
                store[key]["point"] = float(point)

    for bk in books:
        title = bk.get("title") or bk.get("key") or "?"
        for m in bk.get("markets") or []:
            mkey = m.get("key")
            for o in m.get("outcomes") or []:
                name, price = o.get("name"), o.get("price")
                if mkey == "h2h":
                    if name == home_name:
                        _better(h2h, "home", price, title)
                    elif name == away_name:
                        _better(h2h, "away", price, title)
                    elif (name or "").lower() == "draw":
                        _better(h2h, "draw", price, title)
                elif mkey == "totals" and abs(float(o.get("point", 0)) - 2.5) < 1e-9:
                    if (name or "").lower() == "over":
                        _better(totals, "over", price, title, 2.5)
                    elif (name or "").lower() == "under":
                        _better(totals, "under", price, title, 2.5)

    return {"h2h": h2h, "totals": totals, "n_books": len(books)}


# ---------------------------------------------------------------------------
# Appariement d'un match (noms API <-> noms canoniques de la base).
# ---------------------------------------------------------------------------
def _canon(label: str) -> str:
    return names._norm(label)


def _override(label: str) -> str | None:
    return TEAM_OVERRIDES.get(_canon(label))


def find_event(events: list[dict], home: str, away: str,
               min_score: float = 0.6) -> dict | None:
    """Retrouve l'événement API correspondant à (home, away) par appariement flou.

    Teste les deux ordres (l'API peut désigner « home » différemment de nous) et
    renvoie le meilleur si le score combiné dépasse le seuil.
    """
    if not events:
        return None
    h_t, a_t = _override(home) or home, _override(away) or away
    best, best_score, best_swap = None, 0.0, False
    for ev in events:
        eh, ea = ev.get("home_team", ""), ev.get("away_team", "")
        direct = min(names._similarity(h_t, eh), names._similarity(a_t, ea))
        swap = min(names._similarity(h_t, ea), names._similarity(a_t, eh))
        score, swapped = (direct, False) if direct >= swap else (swap, True)
        if score > best_score:
            best, best_score, best_swap = ev, score, swapped
    if best is None or best_score < min_score:
        return None
    return {**best, "_matched_score": round(best_score, 3), "_swapped": best_swap}
