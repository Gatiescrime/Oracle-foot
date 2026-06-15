"""API FastAPI : expose le service de prédiction et sert le frontend.

Endpoints :
  GET  /api/teams?domain=club|international   -> liste des équipes (id, nom, elo)
  GET  /api/competitions                      -> compétitions disponibles
  POST /api/predict                           -> prédiction complète (contrat de sortie)
  GET  /api/fixtures                          -> matchs à venir (mode Coupe du Monde 2026)
  GET  /                                       -> page web (webapp/index.html)
"""

from __future__ import annotations

import os
from functools import lru_cache

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import badges, config, db, refresh_job, service, simulate

app = FastAPI(title="Prédiction Foot", version="1.0")

WEBAPP_DIR = os.path.join(config.ROOT, "webapp")


class PredictRequest(BaseModel):
    competition: str
    home: str
    away: str
    neutral: bool = False
    use_qualitative: bool = False


class ScorersRequest(BaseModel):
    competition: str
    home: str
    away: str
    neutral: bool = False
    top_n: int = 8
    use_qualitative: bool = False
    unavailable_home: list[str] = []
    unavailable_away: list[str] = []


class StakeRequest(BaseModel):
    competition: str
    home: str
    away: str
    selection: str          # home | draw | away | over25 | under25 | btts | btts_no
    odds: float             # cote décimale vue chez le bookmaker
    bankroll: float = 100.0
    neutral: bool = False
    use_qualitative: bool = False


@app.get("/api/competitions")
def competitions() -> dict:
    club = [lg["competition"] for lg in config.CLUB_LEAGUES]
    intl = ["FIFA World Cup", "UEFA Euro", "Copa America", "UEFA Nations League",
            "FIFA World Cup qualification", "Friendly"]
    return {"club": club, "international": intl}


@app.get("/api/teams")
def teams(domain: str | None = None) -> dict:
    if domain and domain not in (config.DOMAIN_CLUB, config.DOMAIN_INTL):
        raise HTTPException(400, "domain invalide")
    return {"teams": service.list_teams(domain)}


@app.post("/api/predict")
def predict(req: PredictRequest) -> dict:
    try:
        return service.predict(req.competition, req.home, req.away, req.neutral,
                               use_qualitative=req.use_qualitative)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except FileNotFoundError:
        raise HTTPException(503, "Modèles non entraînés : lancez python -m pipeline.train")


@app.post("/api/scorers")
def scorers(req: ScorersRequest) -> dict:
    """Buteurs probables (anytime scorer) d'un match de club.

    Sélections : renvoie available=False (données joueur insuffisantes).
    """
    try:
        return service.predict_scorers(
            req.competition, req.home, req.away, req.neutral, top_n=req.top_n,
            use_qualitative=req.use_qualitative,
            unavailable_home=req.unavailable_home, unavailable_away=req.unavailable_away)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except FileNotFoundError:
        raise HTTPException(503, "Modèles non entraînés : lancez python -m pipeline.train")


@app.post("/api/stake")
def stake(req: StakeRequest) -> dict:
    """Mise recommandée (Kelly fractionné plafonné) pour une issue à une cote donnée.

    Sans value, la réponse le dit clairement et ne conseille aucune mise.
    """
    try:
        return service.recommend_stake(
            req.competition, req.home, req.away, req.selection, req.odds,
            bankroll=req.bankroll, neutral=req.neutral,
            use_qualitative=req.use_qualitative)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except FileNotFoundError:
        raise HTTPException(503, "Modèles non entraînés : lancez python -m pipeline.train")


@app.get("/api/odds/live")
def odds_live(competition: str, home: str, away: str, neutral: bool = False,
              use_qualitative: bool = False) -> dict:
    """Meilleures cotes multi-bookmakers + value pour une affiche (Phase P4).

    Source live the-odds-api si une clé est configurée ; sinon repli propre sur
    les cotes football-data en base. La clé API n'est jamais renvoyée.
    """
    try:
        return service.live_odds(competition, home, away, neutral=neutral,
                                 use_qualitative=use_qualitative)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except FileNotFoundError:
        raise HTTPException(503, "Modèles non entraînés : lancez python -m pipeline.train")


@app.get("/api/odds/status")
def odds_status() -> dict:
    """État du comparateur de cotes : clé présente ?, quota, cache, compétitions couvertes."""
    return service.odds_status()


@app.get("/api/odds/clv")
def odds_clv() -> dict:
    """Suivi de ligne capté (proxy de CLV) sur les cotes live déjà observées."""
    return service.odds_clv_summary()


@app.get("/api/qualitative/status")
def qualitative_status() -> dict:
    """État de la couche actualité (défaut, compteur d'appels du jour, garde-fous coût)."""
    return service.qualitative_status()


@lru_cache(maxsize=4)
def _cached_simulation(n_sims: int) -> dict:
    return simulate.simulate_world_cup(n_sims=n_sims)


@app.get("/api/simulate")
def simulate_wc(n_sims: int = 2000) -> dict:
    """Simulation Monte-Carlo de la Coupe du Monde 2026 (probabilités par équipe).

    Coûteux (matrices précalculées + milliers de tournois) : résultat mis en cache.
    """
    n_sims = max(100, min(5000, n_sims))
    res = _cached_simulation(n_sims)
    if "error" in res:
        raise HTTPException(503, res["error"])
    # Écussons (sélections → drapeaux) sans muter le résultat mis en cache.
    teams = [{**t, "badge": badges.badge(t.get("team"), config.DOMAIN_INTL)}
             for t in res.get("teams", [])]
    return {**res, "teams": teams}


@app.post("/api/refresh")
def trigger_refresh(background_tasks: BackgroundTasks, mode: str = "rapide") -> dict:
    """Lance une mise à jour des données EN TÂCHE DE FOND (réponse immédiate).

    mode = "rapide" (données + features) ou "complet" (+ réentraînement des modèles).
    """
    mode = "complet" if mode == "complet" else "rapide"
    if not refresh_job.start(mode):
        raise HTTPException(409, "Une mise à jour est déjà en cours.")
    background_tasks.add_task(refresh_job.run, mode)
    return {"state": "running", "mode": mode}


@app.get("/api/refresh/status")
def refresh_status() -> dict:
    """Avancement de la mise à jour (idle/running/done/error) + dernière MAJ."""
    return refresh_job.status()


@app.post("/api/refresh/cancel")
def cancel_refresh() -> dict:
    """Annule la mise à jour en cours (le job de fond est abandonné proprement).

    Renvoie 409 si aucune mise à jour n'est en cours.
    """
    if not refresh_job.cancel():
        raise HTTPException(409, "Aucune mise à jour en cours.")
    return refresh_job.status()


@app.get("/api/meta")
def meta() -> dict:
    """Métadonnées légères pour l'en-tête (date de dernière mise à jour des données)."""
    return service.app_meta()


@app.get("/api/upcoming")
def upcoming(days: int = 7) -> dict:
    """Affiches à venir (toutes compétitions couvertes) des `days` prochains jours.

    Source live the-odds-api si une clé est configurée (avec meilleures cotes),
    sinon repli propre sur la table `fixtures`. Résultat mis en cache (quota).
    """
    return service.upcoming_matches(days)


@app.get("/api/track-record")
def track_record() -> dict:
    """Performances réelles du backtest walk-forward (RPS, calibration, ROI value).

    Données issues de `data/backtest_result.json` : jamais inventées, honnêtes sur
    le fait que le modèle ne bat pas encore le bookmaker sur les clubs.
    """
    return service.track_record()


@app.get("/api/value/today")
def value_today(days: int = 3, top_n: int = 3) -> dict:
    """Meilleures value parmi les affiches à venir (edge modèle vs meilleure cote).

    Réutilise le comparateur de cotes live. Une value n'est jamais une garantie de
    gain. Renvoie une liste vide si aucune opportunité n'est trouvée.
    """
    return service.best_value_today(days, top_n)


@app.get("/api/fixtures")
def fixtures() -> dict:
    conn = db.connect()
    df = pd.read_sql_query(
        """SELECT f.date, f.competition, f.neutral,
                  th.canonical_name AS home, ta.canonical_name AS away,
                  f.home_team_id, f.away_team_id
           FROM fixtures f
           JOIN teams th ON th.team_id = f.home_team_id
           JOIN teams ta ON ta.team_id = f.away_team_id
           ORDER BY f.date""", conn)
    conn.close()
    rows = df.to_dict(orient="records")
    for r in rows:
        domain = service.domain_of(r["competition"])
        r["home_badge"] = badges.badge(r["home"], domain)
        r["away_badge"] = badges.badge(r["away"], domain)
    return {"fixtures": rows}


# --- frontend statique -----------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(WEBAPP_DIR, "index.html"))


@app.get("/manifest.json")
def manifest():
    """Manifest PWA (servi à la racine pour un scope d'installation global)."""
    return FileResponse(os.path.join(WEBAPP_DIR, "manifest.json"),
                        media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    """Service worker servi à la racine (scope « / ») pour contrôler toute l'app."""
    return FileResponse(
        os.path.join(WEBAPP_DIR, "sw.js"), media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


if os.path.isdir(WEBAPP_DIR):
    app.mount("/static", StaticFiles(directory=WEBAPP_DIR), name="static")
