"""Couche qualitative consciente de l'actualité (Phases 5 + 7), optionnelle et bornée.

Principe (règle d'or n°5) : le LLM **ne prédit pas le score**. Il extrait des FAITS
récents (joueurs clés indisponibles, suspensions, changement d'entraîneur, enjeu) et
en déduit un petit ajustement multiplicatif des buts attendus, **plafonné à ±25 %**.
Le socle statistique reste maître ; sans cette couche, le système tourne seul.

Phase 7 — le modèle va VRAIMENT chercher l'info en ligne :
  1. EXTRACTION (modèle Haiku) AVEC l'outil web_search de l'API Anthropic. Le modèle
     cherche l'actualité des deux équipes sur les ~7 derniers jours. Chaque fait porte
     sa DATE et l'URL de sa SOURCE. On ne garde que les faits datés dans la fenêtre.
  2. SYNTHÈSE (modèle Sonnet) : transforme ces faits datés en multiplicateurs bornés.

Replis (jamais de crash) :
  - si le proxy ne relaie pas web_search : on logue, puis on tente un fournisseur de
    recherche d'actu configurable (.env NEWS_SEARCH_URL/KEY) ;
  - en l'absence de toute info fiable : AUCUN ajustement (on rejoue le stat seul) ;
  - si le réseau tombe mais qu'une entrée en cache existe (même périmée) : on la sert.

Cache : clé (compétition|dom|ext|date) + TTL (réinterrogation au-delà de N heures, car
l'actu évolue). L'entrée périmée reste un filet de secours en cas de réseau KO.

Honnêteté : cette couche NE SE BACKTESTE PAS (pas d'historique de news horodaté).
Validation prospective uniquement. Off par défaut tant qu'elle n'a pas fait ses preuves.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from . import config, db

log = logging.getLogger("pipeline.qualitative")

_EXTRACTION_PROMPT_WEB = """Tu es un assistant factuel disposant de la recherche web. \
Pour le match de football {home} (domicile) contre {away} (extérieur) en {competition}, \
prévu autour du {ref_date}, RECHERCHE EN LIGNE l'actualité des {window} derniers jours \
des deux équipes et liste UNIQUEMENT des faits récents, concrets et vérifiables :
- joueurs clés indisponibles (blessure, suspension) : nom + raison ;
- changement récent d'entraîneur ;
- enjeu particulier (qualification, derby, match décisif) ;
- fatigue manifeste (calendrier surchargé).
Pour CHAQUE fait, donne sa DATE (AAAA-MM-JJ) et l'URL de la SOURCE consultée.
NE DONNE AUCUN PRONOSTIC, AUCUNE PROBABILITÉ, AUCUN SCORE.
Réponds STRICTEMENT en JSON, sans texte autour : une liste d'objets de la forme
[{{"fait": "<court>", "equipe": "{home}" ou "{away}", "date": "AAAA-MM-JJ", \
"source_titre": "<média>", "source_url": "https://..."}}]
Si tu n'as AUCUNE information fiable et datée, renvoie une liste vide [] ."""

_EXTRACTION_PROMPT_SNIPPETS = """Tu es un assistant factuel. À partir des ARTICLES \
d'actualité ci-dessous concernant {home} (domicile) contre {away} (extérieur) en \
{competition} (match autour du {ref_date}), extrais UNIQUEMENT des faits récents, \
concrets et vérifiables (indisponibilités de joueurs clés avec nom + raison, changement \
d'entraîneur, enjeu, fatigue). Pour chaque fait, reprends sa DATE et l'URL de la source.
NE DONNE AUCUN PRONOSTIC, AUCUNE PROBABILITÉ, AUCUN SCORE.
Réponds STRICTEMENT en JSON : une liste d'objets
[{{"fait": "<court>", "equipe": "{home}" ou "{away}", "date": "AAAA-MM-JJ", \
"source_titre": "<média>", "source_url": "https://..."}}]
Si rien de fiable, renvoie [] .

ARTICLES :
{articles}"""

_SYNTHESIS_PROMPT = """À partir des FAITS DATÉS ci-dessous sur {home} (domicile) contre \
{away} (extérieur), estime un léger ajustement des buts attendus de chaque équipe.
Règles strictes :
- les multiplicateurs sont centrés sur 1.0 et bornés dans [{lo}, {hi}] ;
- 1.0 = aucun changement ; <1.0 = équipe affaiblie ; >1.0 = équipe renforcée ;
- si les faits sont minces ou neutres, reste proche de 1.0 et mets une confiance basse.
Réponds STRICTEMENT en JSON, sans texte autour :
{{"mult_dom": <float>, "mult_ext": <float>, "facteurs": ["<court>", ...], "confiance": <0..1>}}

FAITS :
{faits}"""


def _clamp(x: float) -> float:
    m = config.QUALITATIVE_MAX_ADJ
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 1.0
    if x != x:  # NaN
        return 1.0
    return max(1.0 - m, min(1.0 + m, x))


class QualitativeLayer:
    """Encapsule l'appel LLM (avec recherche web), le cache TTL et les garde-fous."""

    def __init__(self, enabled: bool | None = None, client=None,
                 conn_factory=db.connect):
        self.enabled = config.QUALITATIVE_LAYER_ENABLED if enabled is None else enabled
        self._client = client          # injectable (tests)
        self._conn_factory = conn_factory

    # --- client LLM (paresseux) --------------------------------------------
    def _get_client(self):
        if self._client is not None:
            return self._client
        from anthropic import Anthropic
        self._client = Anthropic(base_url=config.ANTHROPIC_BASE_URL or None,
                                 api_key=config.ANTHROPIC_API_KEY or None)
        return self._client

    # --- cache (avec TTL) --------------------------------------------------
    def _cache_key(self, home, away, competition, ref_date) -> str:
        return f"{competition}|{home}|{away}|{ref_date}"

    def _cache_get(self, key):
        """Renvoie {'payload', 'age_hours'} ou None. L'âge sert au TTL."""
        try:
            conn = self._conn_factory()
            conn.execute(
                "CREATE TABLE IF NOT EXISTS news_cache (cache_key TEXT PRIMARY KEY, "
                "created_at TEXT NOT NULL, payload TEXT NOT NULL)")
            row = conn.execute(
                "SELECT created_at, payload FROM news_cache WHERE cache_key = ?",
                (key,)).fetchone()
            conn.close()
            if not row:
                return None
            created = _safe_dt(row[0])
            age = ((datetime.now(timezone.utc) - created).total_seconds() / 3600.0
                   if created else 1e9)
            return {"payload": json.loads(row[1]), "age_hours": age}
        except Exception as e:  # cache best-effort, jamais bloquant
            log.debug("cache_get a échoué : %s", e)
            return None

    def _cache_put(self, key, payload):
        try:
            conn = self._conn_factory()
            conn.execute(
                "CREATE TABLE IF NOT EXISTS news_cache (cache_key TEXT PRIMARY KEY, "
                "created_at TEXT NOT NULL, payload TEXT NOT NULL)")
            conn.execute(
                "INSERT OR REPLACE INTO news_cache(cache_key, created_at, payload) VALUES (?,?,?)",
                (key, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 json.dumps(payload, ensure_ascii=False)))
            conn.commit()
            conn.close()
        except Exception as e:
            log.debug("cache_put a échoué : %s", e)

    # --- compteur d'appels API du jour (garde-fou coût) --------------------
    def _record_api_call(self, n: int = 1) -> None:
        """Incrémente le compteur d'appels LLM réels du jour (best-effort)."""
        day = date.today().isoformat()
        try:
            conn = self._conn_factory()
            conn.execute(
                "CREATE TABLE IF NOT EXISTS api_usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL)")
            conn.execute(
                "INSERT INTO api_usage(day, calls) VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET calls = calls + ?",
                (day, n, n))
            conn.commit()
            conn.close()
        except Exception as e:  # compteur best-effort, jamais bloquant
            log.debug("compteur d'appels indisponible : %s", e)

    def calls_today(self) -> int:
        """Nombre d'appels LLM réels effectués aujourd'hui (0 si indisponible)."""
        day = date.today().isoformat()
        try:
            conn = self._conn_factory()
            conn.execute(
                "CREATE TABLE IF NOT EXISTS api_usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL)")
            row = conn.execute(
                "SELECT calls FROM api_usage WHERE day = ?", (day,)).fetchone()
            conn.close()
            return int(row[0]) if row else 0
        except Exception as e:
            log.debug("lecture du compteur impossible : %s", e)
            return 0

    # --- récolte des faits (web puis repli) --------------------------------
    def _gather_facts(self, home, away, competition, ref_date):
        """Renvoie une liste de faits (éventuellement vide), ou None si AUCUNE
        recherche n'a pu aboutir (web non relayé ET aucun fournisseur de repli)."""
        text, source = self._extract_via_web(home, away, competition, ref_date), "claude+web"
        if text is None:
            snippets = _news_provider_search(f"{home} {away} {competition} actualité",
                                             config.QUALITATIVE_NEWS_WINDOW_DAYS)
            if snippets is None:
                return None, None        # aucun moyen de chercher en ligne
            text = self._extract_from_snippets(home, away, competition, ref_date, snippets)
            source = f"claude+{config.NEWS_SEARCH_PROVIDER or 'news'}"
        return _parse_facts(text), source

    def _extract_via_web(self, home, away, competition, ref_date):
        """Extraction AVEC l'outil web_search. None si web indisponible/non relayé."""
        if not config.QUALITATIVE_WEB_SEARCH_ENABLED:
            return None
        prompt = _EXTRACTION_PROMPT_WEB.format(
            home=home, away=away, competition=competition, ref_date=ref_date,
            window=config.QUALITATIVE_NEWS_WINDOW_DAYS)
        try:
            msg = self._get_client().messages.create(
                model=config.QUALITATIVE_MODEL_EXTRACTION,
                max_tokens=1200,
                tools=[{"type": config.QUALITATIVE_WEB_SEARCH_TOOL,
                        "name": "web_search",
                        "max_uses": config.QUALITATIVE_WEB_SEARCH_MAX_USES}],
                messages=[{"role": "user", "content": prompt}])
            self._record_api_call()
        except Exception as e:
            log.warning("Outil web_search indisponible via le proxy (%s) : repli.", e)
            return None
        if not _used_web_search(msg):
            log.warning("Le proxy n'a pas relayé web_search (aucun appel d'outil) : "
                        "repli sur fournisseur de news.")
            return None
        return _text_of(msg)

    def _extract_from_snippets(self, home, away, competition, ref_date, snippets) -> str:
        blob = "\n".join(
            f"- ({s.get('date', '')}) {s.get('title', '')} — {s.get('snippet', '')} "
            f"[{s.get('url', '')}]" for s in snippets) or "(aucun)"
        msg = self._get_client().messages.create(
            model=config.QUALITATIVE_MODEL_EXTRACTION,
            max_tokens=1200,
            messages=[{"role": "user", "content": _EXTRACTION_PROMPT_SNIPPETS.format(
                home=home, away=away, competition=competition, ref_date=ref_date,
                articles=blob)}])
        self._record_api_call()
        return _text_of(msg)

    def _filter_recent(self, facts, ref_date):
        """Ne garde que les faits datés dans la fenêtre [ref - N jours, ref + 1 j]."""
        ref_d = _safe_date(ref_date) or date.today()
        lo = ref_d - timedelta(days=config.QUALITATIVE_NEWS_WINDOW_DAYS)
        hi = ref_d + timedelta(days=1)
        kept = []
        for f in facts:
            d = _safe_date(f.get("date"))
            if d is None or not (lo <= d <= hi):
                continue
            if not str(f.get("source_url", "")).startswith("http"):
                continue          # un fait sans source vérifiable est écarté
            kept.append({
                "fait": str(f.get("fait", ""))[:200],
                "equipe": str(f.get("equipe", "")),
                "date": d.isoformat(),
                "source_titre": str(f.get("source_titre", ""))[:120],
                "source_url": str(f.get("source_url", "")),
            })
        return kept[:8]

    # --- synthèse ----------------------------------------------------------
    def _synthesise(self, home, away, faits) -> dict:
        lo = round(1.0 - config.QUALITATIVE_MAX_ADJ, 2)
        hi = round(1.0 + config.QUALITATIVE_MAX_ADJ, 2)
        faits_txt = "\n".join(
            f"- [{f['date']}] ({f['equipe']}) {f['fait']} "
            f"(source : {f['source_titre']} {f['source_url']})" for f in faits)
        msg = self._get_client().messages.create(
            model=config.QUALITATIVE_MODEL_SYNTHESIS,
            max_tokens=400,
            messages=[{"role": "user", "content": _SYNTHESIS_PROMPT.format(
                home=home, away=away, lo=lo, hi=hi, faits=faits_txt)}])
        self._record_api_call()
        return _parse_json(_text_of(msg))

    # --- API publique ------------------------------------------------------
    def adjust(self, home: str, away: str, competition: str,
               ref_date=None, enabled_override: bool | None = None) -> dict | None:
        """Renvoie l'ajustement borné, ou None si couche off / aucune info fiable.

        `enabled_override` permet d'activer/désactiver la couche par requête depuis
        l'UI, sans toucher au .env : True force l'exécution, False la désactive,
        None retombe sur la valeur par défaut du processus (config).

        Forme : {mult_dom, mult_ext, facteurs:[...], faits:[{fait,date,source_*}],
                 confiance, source, as_of}.
        """
        active = self.enabled if enabled_override is None else bool(enabled_override)
        if not active:
            return None
        ref = str(ref_date or date.today())
        key = self._cache_key(home, away, competition, ref)

        cached = self._cache_get(key)
        if cached is not None and cached["age_hours"] < config.QUALITATIVE_CACHE_TTL_HOURS:
            return cached["payload"]      # entrée fraîche : on la sert

        try:
            facts, source = self._gather_facts(home, away, competition, ref)
        except Exception as e:
            log.warning("Récolte des faits impossible (%s).", e)
            facts, source = None, None

        if facts is None:                 # aucune recherche n'a pu aboutir
            if cached is not None:
                log.info("Réseau/outil KO : repli sur l'entrée de cache périmée.")
                return cached["payload"]
            return None

        facts = self._filter_recent(facts, ref)
        if not facts:                     # aucun fait daté fiable -> aucun ajustement
            log.info("Aucun fait récent daté pour %s-%s : aucun ajustement.", home, away)
            return None

        try:
            raw = self._synthesise(home, away, facts)
        except Exception as e:
            log.warning("Synthèse indisponible (%s).", e)
            if cached is not None:
                return cached["payload"]
            return None

        result = {
            "mult_dom": _clamp(raw.get("mult_dom", 1.0)),
            "mult_ext": _clamp(raw.get("mult_ext", 1.0)),
            "facteurs": [str(x) for x in raw.get("facteurs", [])][:6],
            "faits": facts,
            "confiance": max(0.0, min(1.0, float(raw.get("confiance", 0.0) or 0.0))),
            "source": source,
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._cache_put(key, result)
        return result


# --- fournisseur de news de repli (HTTP générique) -------------------------
def _news_provider_search(query: str, window_days: int):
    """Repli : interroge un fournisseur de recherche d'actu configuré par .env.

    Renvoie une liste de {title, url, date, snippet}, ou None si non configuré /
    indisponible (jamais d'exception remontée)."""
    if not config.NEWS_SEARCH_URL or not config.NEWS_SEARCH_API_KEY:
        return None
    try:
        q = urllib.parse.urlencode({"q": query, "days": window_days})
        req = urllib.request.Request(
            f"{config.NEWS_SEARCH_URL}?{q}",
            headers={"Authorization": f"Bearer {config.NEWS_SEARCH_API_KEY}",
                     "X-Api-Key": config.NEWS_SEARCH_API_KEY,
                     "User-Agent": config.USER_AGENT})
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        items = data.get("results") or data.get("articles") or []
        out = []
        for it in items[:10]:
            out.append({
                "title": it.get("title") or it.get("source_titre", ""),
                "url": it.get("url") or it.get("source_url", ""),
                "date": str(it.get("published_date") or it.get("date") or "")[:10],
                "snippet": it.get("snippet") or it.get("description", ""),
            })
        return out
    except Exception as e:
        log.warning("Fournisseur de news de repli indisponible (%s).", e)
        return None


# --- helpers ---------------------------------------------------------------
def _used_web_search(msg) -> bool:
    """Vrai si la réponse contient un appel d'outil web_search côté serveur."""
    for b in getattr(msg, "content", []) or []:
        if getattr(b, "type", "") in ("server_tool_use", "web_search_tool_result"):
            return True
    return False


def _text_of(msg) -> str:
    parts = getattr(msg, "content", []) or []
    return "".join(getattr(b, "text", "") or "" for b in parts).strip()


def _parse_json(text: str) -> dict:
    """Extrait le premier objet JSON d'une réponse (tolère du texte autour)."""
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1 or e < s:
        raise ValueError("réponse sans JSON")
    return json.loads(text[s:e + 1])


def _parse_facts(text: str) -> list:
    """Extrait la liste JSON de faits d'une réponse (tolère du texte/citations autour)."""
    if text is None:
        return []
    s, e = text.find("["), text.rfind("]")
    if s == -1 or e == -1 or e < s:
        return []
    try:
        arr = json.loads(text[s:e + 1])
    except Exception:
        return []
    return [f for f in arr if isinstance(f, dict)]


def _safe_date(x):
    """Parse une date ISO (AAAA-MM-JJ) tolérante ; None si invalide."""
    if not x:
        return None
    try:
        return date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def _safe_dt(x):
    """Parse un datetime ISO (UTC) ; None si invalide."""
    if not x:
        return None
    try:
        dt = datetime.fromisoformat(str(x))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
