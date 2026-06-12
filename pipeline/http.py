"""Téléchargement réseau robuste : retries, cache disque, fallback, logs.

Toute requête passe par `fetch`. Le résultat brut est mis en cache dans
`data/cache/`. Si le réseau échoue, on retombe sur le cache même périmé, pour que
le pipeline reste utilisable hors ligne une fois amorcé.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import time
import urllib.request
import zlib

from . import config

log = logging.getLogger("pipeline.http")


def _cache_path(cache_key: str) -> str:
    h = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:20]
    return os.path.join(config.CACHE_DIR, f"{h}.bin")


def _cache_age_hours(path: str) -> float:
    return (time.time() - os.path.getmtime(path)) / 3600.0


def _decompress(raw: bytes, encoding: str | None) -> bytes:
    if not encoding:
        return raw
    encoding = encoding.lower()
    try:
        if "gzip" in encoding:
            return gzip.decompress(raw)
        if "deflate" in encoding:
            return zlib.decompress(raw)
        if "br" in encoding:
            import brotli  # type: ignore

            return brotli.decompress(raw)
    except Exception as e:  # noqa: BLE001
        log.warning("Décompression %s échouée (%s), contenu brut conservé", encoding, e)
    return raw


def fetch(url: str, *, cache_key: str | None = None, ttl_hours: float | None = None,
          headers: dict | None = None, use_cache: bool = True,
          on_headers=None) -> bytes:
    """Télécharge `url` et renvoie les octets décompressés.

    - Utilise le cache si présent et plus frais que `ttl_hours`.
    - En cas d'échec réseau, retombe sur le cache même périmé s'il existe.
    - `on_headers(resp.headers)` : rappel optionnel sur un téléchargement réseau
      réussi (ex. lire un quota d'API). Jamais appelé sur un hit de cache.
    """
    cache_key = cache_key or url
    ttl = config.CACHE_TTL_HOURS if ttl_hours is None else ttl_hours
    cpath = _cache_path(cache_key)

    if use_cache and os.path.exists(cpath) and _cache_age_hours(cpath) < ttl:
        log.info("cache hit (%.1fh) %s", _cache_age_hours(cpath), url)
        with open(cpath, "rb") as f:
            return f.read()

    req_headers = {
        "User-Agent": config.USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        req_headers.update(headers)

    last_err: Exception | None = None
    for attempt in range(config.HTTP_RETRIES):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
                raw = resp.read()
                data = _decompress(raw, resp.headers.get("Content-Encoding"))
                if on_headers is not None:
                    try:
                        on_headers(resp.headers)
                    except Exception:  # noqa: BLE001
                        pass
            with open(cpath, "wb") as f:
                f.write(data)
            log.info("téléchargé %d o depuis %s", len(data), url)
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("échec %s (essai %d/%d): %s", url, attempt + 1, config.HTTP_RETRIES, e)
            time.sleep(2 * (attempt + 1))

    if os.path.exists(cpath):
        log.warning("réseau KO, fallback cache périmé pour %s", url)
        with open(cpath, "rb") as f:
            return f.read()

    raise RuntimeError(f"Téléchargement impossible et aucun cache pour {url}: {last_err}")
