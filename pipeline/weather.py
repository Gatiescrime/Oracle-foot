"""Météo des matchs via Open-Meteo (Phase P6), surtout pour les marchés over/under.

Open-Meteo propose une archive historique SANS clé d'API. Pour chaque lieu (stade),
on télécharge en UN appel toute la plage de dates utile (valeurs quotidiennes), puis
on apparie chaque match à sa date. Résultat mis en cache disque (TTL long).

Features produites (au lieu du match, identiques pour les deux équipes) :
  - `temp_c`    : température moyenne du jour (°C) ;
  - `precip_mm` : précipitations cumulées du jour (mm) ;
  - `wind_kmh`  : vent maximal du jour (km/h).

Anti-fuite : la météo dépend du LIEU et de la DATE du match, tous deux connus avant
le coup d'envoi. Aucune information de résultat. Hors ligne, météo désactivée, ou
lieu inconnu -> aucune clé renvoyée pour le match (l'appelant produit NaN).

Note : l'archive historique accuse un léger retard (quelques jours) et ne couvre pas
les dates futures. Un match à venir n'a donc pas de météo d'archive -> NaN propre
(la prévision n'est pas utilisée ici pour rester simple et reproductible).
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict

from . import config

log = logging.getLogger(__name__)

# Variables quotidiennes demandées à Open-Meteo, dans l'ordre de mapping ci-dessous.
_DAILY_VARS = "temperature_2m_mean,precipitation_sum,wind_speed_10m_max"

# Arrondi des coordonnées pour regrouper les lieux quasi identiques (mutualise les
# appels : deux clubs d'une même ville partagent souvent ~la même météo quotidienne).
_COORD_DP = 2


def _round_coord(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, _COORD_DP), round(lon, _COORD_DP))


def _fetch_venue(lat: float, lon: float, start: str, end: str) -> tuple[dict[str, dict], bool]:
    """Télécharge la plage [start, end] pour un lieu -> ({date_iso: feats}, réseau?).

    Le second élément indique si un appel RÉSEAU a eu lieu (vs hit de cache), pour
    permettre à l'appelant d'espacer les requêtes (Open-Meteo limite les rafales).
    Repli propre (dict vide) en cas d'échec réseau ou de réponse inattendue.
    """
    from . import http

    url = (
        f"{config.WEATHER_ARCHIVE_URL}?latitude={lat}&longitude={lon}"
        f"&start_date={start}&end_date={end}&daily={_DAILY_VARS}&timezone=UTC"
    )
    cache_key = f"weather_{lat}_{lon}_{start}_{end}"
    hit = {"net": False}
    try:
        raw = http.fetch(url, cache_key=cache_key, ttl_hours=config.WEATHER_TTL_HOURS,
                         on_headers=lambda _h: hit.__setitem__("net", True))
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:  # repli silencieux : pas de météo pour ce lieu
        log.warning("weather: lieu (%.2f,%.2f) indisponible (%s)", lat, lon, exc)
        return {}, True  # un échec réseau compte comme "réseau" -> on ralentit aussi

    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    temps = daily.get("temperature_2m_mean") or []
    precs = daily.get("precipitation_sum") or []
    winds = daily.get("wind_speed_10m_max") or []
    out: dict[str, dict] = {}
    for i, day in enumerate(times):
        def _val(arr):
            v = arr[i] if i < len(arr) else None
            return float(v) if v is not None else None
        out[day] = {
            "temp_c": _val(temps),
            "precip_mm": _val(precs),
            "wind_kmh": _val(winds),
        }
    return out, hit["net"]


def weather_by_match(rows: list[dict]) -> dict[str, dict]:
    """{match_id: {temp_c, precip_mm, wind_kmh}} pour des matchs localisés.

    `rows` : liste de dicts {match_id, lat, lon, date} (date au format ISO
    'YYYY-MM-DD'). On regroupe par lieu, télécharge chaque plage une seule fois,
    puis apparie par date. Météo désactivée -> dict vide. Match sans météo
    disponible (lieu inconnu en amont, date future, échec réseau) -> pas de clé.
    """
    if not config.WEATHER_ENABLED or not rows:
        return {}

    # Regroupe les dates par lieu arrondi.
    by_venue: dict[tuple, list] = defaultdict(list)
    for r in rows:
        lat, lon, date = r.get("lat"), r.get("lon"), r.get("date")
        if lat is None or lon is None or not date:
            continue
        by_venue[_round_coord(lat, lon)].append((r["match_id"], date))

    result: dict[str, dict] = {}
    delay = max(0.0, config.WEATHER_MIN_INTERVAL_S)
    for (lat, lon), items in by_venue.items():
        dates = [d for _, d in items]
        start, end = min(dates), max(dates)
        table, used_net = _fetch_venue(lat, lon, start, end)
        # Espace les appels RÉSEAU (Open-Meteo limite les rafales) ; un hit de
        # cache n'attend pas -> les exécutions suivantes restent rapides.
        if used_net and delay:
            time.sleep(delay)
        if not table:
            continue
        for match_id, date in items:
            feats = table.get(date)
            if feats is not None:
                result[match_id] = feats
    return result
