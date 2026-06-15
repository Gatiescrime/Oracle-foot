"""Configuration centrale du pipeline de données.

Tout ce qui dépend de l'environnement (chemins) ou des sources (ligues, saisons,
URLs) est défini ici, pour que le reste du code n'ait pas de constantes en dur.
"""

from __future__ import annotations

import os
import sys


def _resource_root() -> str:
    """Racine des ressources EN LECTURE (webapp, modèles et base livrés).

    En exécutable PyInstaller, c'est le dossier d'extraction (`sys._MEIPASS`) ;
    sinon, la racine du dépôt.
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _data_home() -> str:
    """Racine des données INSCRIPTIBLES (cache, base mise à jour, logs).

    En exécutable empaqueté, on écrit dans le dossier utilisateur (`~/PredictFoot`)
    car le bundle est en lecture seule / temporaire ; sinon, dans le dépôt.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.expanduser("~"), "PredictFoot")
    return _resource_root()


# --- Chemins ---------------------------------------------------------------
ROOT = _resource_root()                            # ressources livrées (lecture)
DATA_DIR = os.path.join(_data_home(), "data")      # données inscriptibles
RESOURCE_DATA_DIR = os.path.join(ROOT, "data")     # données livrées dans le bundle
CACHE_DIR = os.path.join(DATA_DIR, "cache")
DB_PATH = os.path.join(DATA_DIR, "football.db")
LOG_PATH = os.path.join(DATA_DIR, "refresh.log")
MODELS_DIR = os.path.join(DATA_DIR, "models")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

try:
    from dotenv import load_dotenv
    # override=True : le .env du projet fait foi (proxy + clé), même si le shell
    # exporte déjà ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY (cas fréquent : Claude Code).
    # En exécutable empaqueté, le .env est absent (secret) : load_dotenv ne fait rien
    # et la clé éventuelle provient des variables d'environnement.
    load_dotenv(os.path.join(_resource_root(), ".env"), override=True)
except ImportError:
    pass


def seed_writable_data() -> None:
    """Recopie la base et les modèles livrés vers le dossier inscriptible (1re exécution).

    Utile uniquement en exécutable empaqueté : les ressources du bundle (lecture seule)
    amorcent `~/PredictFoot/data` pour que l'app fonctionne hors ligne dès le 1er lancement.
    Sans effet en développement (les deux chemins sont identiques).
    """
    import shutil

    if os.path.abspath(DATA_DIR) == os.path.abspath(RESOURCE_DATA_DIR):
        return
    src_db = os.path.join(RESOURCE_DATA_DIR, "football.db")
    if os.path.exists(src_db) and not os.path.exists(DB_PATH):
        shutil.copy2(src_db, DB_PATH)
    src_models = os.path.join(RESOURCE_DATA_DIR, "models")
    if os.path.isdir(src_models) and not os.path.exists(os.path.join(MODELS_DIR, "meta_club.json")):
        os.makedirs(MODELS_DIR, exist_ok=True)
        for f in os.listdir(src_models):
            dst = os.path.join(MODELS_DIR, f)
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(src_models, f), dst)

# --- Réseau ----------------------------------------------------------------
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (football-model)"
CACHE_TTL_HOURS = 12          # un fichier en cache plus récent que ça est réutilisé
HTTP_RETRIES = 3
HTTP_TIMEOUT = 45

# --- Mise à jour « rapide » (bouton UI) ------------------------------------
# Le mode rapide ne re-télécharge QUE les sources légères (résultats football-data
# et martj42) ; les sources lentes (xG/joueurs understat) sont réutilisées depuis
# le cache disque tant qu'il est plus frais que ce TTL. But : réponse en quelques
# secondes, jamais bloquée.
QUICK_HEAVY_TTL_HOURS = float(os.environ.get("QUICK_HEAVY_TTL_HOURS", "24"))
# Timeout réseau COURT par source en mode rapide (échec franc plutôt que blocage).
QUICK_HTTP_TIMEOUT = float(os.environ.get("QUICK_HTTP_TIMEOUT", "8"))
QUICK_HTTP_RETRIES = int(os.environ.get("QUICK_HTTP_RETRIES", "1"))
# Timeout GLOBAL DUR du job de mise à jour : au-delà, état « error » propre (le job
# ne peut JAMAIS rester bloqué indéfiniment). Rapide court, complet généreux (train).
REFRESH_TIMEOUT_QUICK_S = float(os.environ.get("REFRESH_TIMEOUT_QUICK_S", "25"))
REFRESH_TIMEOUT_FULL_S = float(os.environ.get("REFRESH_TIMEOUT_FULL_S", "300"))

# --- Ligues de clubs -------------------------------------------------------
# Chaque ligue relie le code football-data, le code understat et un libellé commun.
# competition = libellé canonique stocké dans la base (identique côté matches).
CLUB_LEAGUES = [
    {"fd": "E0",  "understat": "EPL",        "competition": "Premier League", "country": "Angleterre"},
    {"fd": "SP1", "understat": "La_liga",    "competition": "La Liga",        "country": "Espagne"},
    {"fd": "D1",  "understat": "Bundesliga", "competition": "Bundesliga",     "country": "Allemagne"},
    {"fd": "I1",  "understat": "Serie_A",    "competition": "Serie A",        "country": "Italie"},
    {"fd": "F1",  "understat": "Ligue_1",    "competition": "Ligue 1",        "country": "France"},
]

# Saisons : code football-data "2122" = saison 2021/22 = code understat "2021".
# (understat numérote par l'année de début de saison.)
CLUB_SEASONS = [
    {"fd": "2122", "understat": "2021", "label": "2021/22"},
    {"fd": "2223", "understat": "2022", "label": "2022/23"},
    {"fd": "2324", "understat": "2023", "label": "2023/24"},
    {"fd": "2425", "understat": "2024", "label": "2024/25"},
    {"fd": "2526", "understat": "2025", "label": "2025/26"},
]

FD_BASE_URL = "https://www.football-data.co.uk/mmz4281"
UNDERSTAT_URL = "https://understat.com/getLeagueData/{league}/{season}"
INTL_RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
# Buteurs des sélections (même dépôt martj42, noms d'équipes alignés sur results.csv).
INTL_SCORERS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/goalscorers.csv"
# Fenêtre d'AGRÉGATION des buts (reflète l'effectif actif : on ignore l'historique lointain).
INTL_SCORER_SINCE_YEAR = int(os.environ.get("INTL_SCORER_SINCE_YEAR", "2022"))
# Filtre ANTI-RETRAITÉS appliqué à la LECTURE (donc auto-adaptatif dans le temps) :
# un joueur sans but depuis plus de N ans n'est plus proposé.
INTL_SCORER_ACTIVE_YEARS = float(os.environ.get("INTL_SCORER_ACTIVE_YEARS", "3"))

DOMAIN_CLUB = "club"
DOMAIN_INTL = "international"

# --- Effet « pays hôte » en Coupe du Monde (FIXE, non appris -> aucune fuite) ---
# Une sélection qui dispute la Coupe du Monde DANS son propre pays bénéficie d'un
# avantage réel et documenté (public massif, familiarité, longue préparation) :
# historiquement les hôtes surperforment nettement leur classement. On le modélise
# par un bonus FIXE ajouté aux buts attendus de l'hôte, distinct de l'avantage du
# terrain habituel (`home_adv`) et CUMULABLE même en « terrain neutre » (toute la
# Coupe du Monde est jouée sur terrain neutre au sens du modèle).
#
# Valeur retenue : bonus en log-buts = 0,30, soit exp(0,30) ≈ +35 % de buts attendus
# pour l'hôte. Ordre de grandeur volontairement calé sur l'avantage du terrain mesuré
# par le modèle lui-même (home_adv ≈ 0,27 → +31 %) : un hôte joue de fait « à domicile ».
# Choisi A PRIORI (jamais ajusté sur les résultats) pour rester sans fuite.
# Hôtes 2026 : États-Unis, Canada, Mexique (noms canoniques de la base).
WORLD_CUP_COMPETITION = "FIFA World Cup"
WORLD_CUP_HOSTS = {"United States", "Canada", "Mexico"}
HOST_GOAL_LOG_BONUS = float(os.environ.get("HOST_GOAL_LOG_BONUS", "0.30"))

# --- Hyperparamètres Dixon-Coles PAR DOMAINE -------------------------------
# Les CLUBS jouent dans des ligues fermées, bien connectées : réglage standard.
# Les SÉLECTIONS sont mal connectées ENTRE confédérations — une équipe qui domine
# un vivier régional faible (p. ex. l'Égypte en zone Afrique) paraît plus forte
# qu'elle ne l'est face à l'élite européenne/sud-américaine, car les matchs
# inter-confédérations sont rares. On corrige ce biais SANS fuite (poids FIXES,
# non appris) en pondérant davantage les matchs qui « relient » les confédérations
# (Coupe du Monde, Coupe des Confédérations, amicaux souvent intercontinentaux),
# et en relâchant un peu la régularisation pour mieux différencier les niveaux.
# Validé en backtest chronologique : RPS international stable, meilleure
# différenciation (voir docs/modele_intl.md).
DC_PARAMS_CLUB: dict = {}
DC_PARAMS_INTL: dict = {
    # reg=1.5 conservé : il évite les espérances de buts absurdes et les
    # probabilités affichées à 100 % sur les écarts extrêmes (honnêteté). On
    # ajoute UNIQUEMENT la pondération inter-confédérations (le correctif de fond).
    "comp_weight": {
        "FIFA World Cup": 4.0,
        "Confederations Cup": 4.0,
        "Friendly": 1.5,
    },
}


def dc_params(domain: str) -> dict:
    """Hyperparamètres Dixon-Coles à utiliser pour un domaine donné."""
    return dict(DC_PARAMS_INTL if domain == DOMAIN_INTL else DC_PARAMS_CLUB)

# --- Backtest de PARIS (Phase P1) ------------------------------------------
# Métrique de vérité : battre le bookmaker en simulation chronologique anti-fuite.
# Edge minimal requis pour parier : (prob_modèle * cote) - 1 > seuil.
BET_EDGE_THRESHOLD = float(os.environ.get("BET_EDGE_THRESHOLD", "0.05"))
# Fraction de Kelly (mise prudente) : 0.25 = quart de Kelly.
BET_KELLY_FRACTION = float(os.environ.get("BET_KELLY_FRACTION", "0.25"))
# Commission de l'opérateur sur les GAINS NETS (style exchange) ; 0 = bookmaker.
BET_COMMISSION = float(os.environ.get("BET_COMMISSION", "0.0"))
# Mise à plat, en unités de bankroll (1 unité = 1 % du capital initial par défaut).
BET_STAKE_FLAT = float(os.environ.get("BET_STAKE_FLAT", "1.0"))
# Capital initial (en unités).
BET_BANKROLL_0 = float(os.environ.get("BET_BANKROLL_0", "100.0"))
# Plafond de mise Kelly (en unités) pour éviter les paris démesurés.
BET_KELLY_CAP = float(os.environ.get("BET_KELLY_CAP", "5.0"))
# Mise recommandée dans l'UI (Phase P3) : plafonnée à cette FRACTION du capital
# (garde-fou anti-ruine), quel que soit ce que suggère Kelly.
BET_MAX_STAKE_FRAC = float(os.environ.get("BET_MAX_STAKE_FRAC", "0.05"))

# --- Cotes du marché comme signal d'entrée (Phase P2) ----------------------
# Deux leviers, par défaut OFF (le modèle livré reste « pur », sans avis du marché).
# On n'utilise QUE les cotes d'OUVERTURE (pré-match) -> aucune fuite.
#   * features : ajoute les probabilités de marché dévigées aux entrées XGBoost.
MARKET_FEATURES_ENABLED = os.environ.get(
    "MARKET_FEATURES_ENABLED", "false").strip().lower() == "true"
#   * blend : mélange final p = (1-w)*modèle + w*marché (w dans [0,1]).
MARKET_BLEND_WEIGHT = float(os.environ.get("MARKET_BLEND_WEIGHT", "0.0"))

# --- Agrégation de cotes multi-bookmakers en live (Phase P4) ---------------
# the-odds-api.com : compare les prix de dizaines de books pour parier au meilleur
# prix (line shopping) et repérer la value. Clé dans .env, JAMAIS exposée au frontend.
# Sans clé : repli propre sur les cotes football-data déjà en base.
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
ODDS_API_BASE_URL = os.environ.get(
    "ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4").rstrip("/")
ODDS_API_REGIONS = os.environ.get("ODDS_API_REGIONS", "eu,uk").strip()
ODDS_API_MARKETS = os.environ.get("ODDS_API_MARKETS", "h2h,totals").strip()
# Cache des cotes live : économise le quota d'appels (palier gratuit limité).
ODDS_API_TTL_HOURS = float(os.environ.get("ODDS_API_TTL_HOURS", "6"))

# --- Enrichissement données : valeur d'effectif + Elo off/déf (Phase P5) ---
# Trois features supplémentaires, ON par défaut car le backtest walk-forward montre
# un gain honnête et SANS régression sur les deux domaines (RPS clubs 0,2005->0,1996 ;
# sélections 0,1885->0,1880 ; log-loss en baisse ; ROI moins négatif) :
#   * valeur marchande des effectifs (proxy de niveau) ;
#   * Elo séparé en rating OFFENSIF et DÉFENSIF par équipe ;
#   * avantage du terrain SPÉCIFIQUE à la compétition (nul en terrain neutre).
# Contrairement aux cotes de marché (P2), ces features sont TOUTES calculables pour
# un match à venir -> activables sans casser les prédictions futures. Mettre la
# variable à "false" reproduit exactement le modèle d'avant P5.
P5_FEATURES_ENABLED = os.environ.get(
    "P5_FEATURES_ENABLED", "true").strip().lower() == "true"
# Valeur d'effectif : CSV versionné et éditable (millions d'euros), aligné sur nos
# noms canoniques. Une URL optionnelle permet un rafraîchissement (jamais requis).
SQUAD_VALUE_CSV = os.environ.get(
    "SQUAD_VALUE_CSV", os.path.join(RESOURCE_DATA_DIR, "squad_values.csv"))
SQUAD_VALUE_URL = os.environ.get("SQUAD_VALUE_URL", "").strip()

# --- Variables de contexte (Phase P6) --------------------------------------
# Dernières features candidates : qualité de tir affinée (clubs), congestion du
# calendrier, distance de déplacement, et météo (over/under). Discipline anti-
# surapprentissage : on ne garde dans le modèle que les groupes dont le gain est
# prouvé contre P1 (cf. pipeline/p6_eval.py). Le drapeau ci-dessous active les
# COLONNES RETENUES ; les colonnes sont toujours calculables/stockées.
P6_FEATURES_ENABLED = os.environ.get(
    "P6_FEATURES_ENABLED", "true").strip().lower() == "true"
# Coordonnées des lieux (CSV versionné : nom canonique -> latitude, longitude),
# pour la distance de déplacement et la météo. Équipe absente -> features NaN.
VENUES_CSV = os.environ.get(
    "VENUES_CSV", os.path.join(RESOURCE_DATA_DIR, "venues.csv"))
# Météo : Open-Meteo (archive historique + prévision), SANS clé. Mise en cache
# disque ; hors ligne ou sans coordonnées -> NaN propre. Activable séparément.
WEATHER_ENABLED = os.environ.get("WEATHER_ENABLED", "true").strip().lower() == "true"
WEATHER_ARCHIVE_URL = os.environ.get(
    "WEATHER_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1/archive").rstrip("/")
WEATHER_TTL_HOURS = float(os.environ.get("WEATHER_TTL_HOURS", str(24 * 30)))
# Délai (s) entre deux appels RÉSEAU à Open-Meteo (palier gratuit : limite les
# rafales -> HTTP 429). N'affecte QUE le premier remplissage du cache ; ensuite les
# lectures viennent du cache disque, sans attente.
WEATHER_MIN_INTERVAL_S = float(os.environ.get("WEATHER_MIN_INTERVAL_S", "2.0"))

# --- Couche qualitative (Phase 5, optionnelle) -----------------------------
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
# Si la base_url est vide (endpoint officiel voulu), on RETIRE la variable
# d'environnement : sinon le SDK Anthropic lit la chaîne vide et fabrique une URL
# invalide (httpx UnsupportedProtocol) au lieu d'utiliser son défaut officiel.
if not ANTHROPIC_BASE_URL:
    os.environ.pop("ANTHROPIC_BASE_URL", None)
QUALITATIVE_MODEL_EXTRACTION = os.environ.get("QUALITATIVE_MODEL_EXTRACTION", "claude-haiku-4-5-20251001")
QUALITATIVE_MODEL_SYNTHESIS = os.environ.get("QUALITATIVE_MODEL_SYNTHESIS", "claude-sonnet-4-6")
QUALITATIVE_LAYER_ENABLED = os.environ.get("QUALITATIVE_LAYER_ENABLED", "false").strip().lower() == "true"
# Ajustement multiplicatif maximal des buts attendus (garde-fou non négociable).
QUALITATIVE_MAX_ADJ = 0.25

# --- Couche qualitative consciente du web (Phase 7) ------------------------
# Recherche web côté API Anthropic pendant l'extraction des faits.
QUALITATIVE_WEB_SEARCH_ENABLED = os.environ.get(
    "QUALITATIVE_WEB_SEARCH_ENABLED", "true").strip().lower() == "true"
QUALITATIVE_WEB_SEARCH_TOOL = os.environ.get(
    "QUALITATIVE_WEB_SEARCH_TOOL", "web_search_20250305")
QUALITATIVE_WEB_SEARCH_MAX_USES = int(
    os.environ.get("QUALITATIVE_WEB_SEARCH_MAX_USES", "5"))
# Fenêtre d'actualité : on ne retient que les faits datés des N derniers jours.
QUALITATIVE_NEWS_WINDOW_DAYS = int(os.environ.get("QUALITATIVE_NEWS_WINDOW_DAYS", "7"))
# L'actu évolue : on réinterroge si l'entrée en cache a plus de N heures.
QUALITATIVE_CACHE_TTL_HOURS = float(os.environ.get("QUALITATIVE_CACHE_TTL_HOURS", "12"))
# Repli si le proxy ne relaie pas web_search : fournisseur de recherche d'actu HTTP.
# API JSON générique attendue : ?q=...&days=... -> {"results":[{title,url,date,snippet}]}.
NEWS_SEARCH_URL = os.environ.get("NEWS_SEARCH_URL", "")
NEWS_SEARCH_API_KEY = os.environ.get("NEWS_SEARCH_API_KEY", "")
NEWS_SEARCH_PROVIDER = os.environ.get("NEWS_SEARCH_PROVIDER", "")
