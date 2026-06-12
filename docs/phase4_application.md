# Phase 4 — L'application

## En une phrase

Une fonction de prédiction unique, exposée par une petite API, et une page web où
l'on choisit deux équipes (clubs ou sélections, avec un **mode Coupe du Monde 2026**)
pour obtenir probabilités, score le plus probable et matrice des scores.

## Lancer l'application

```bash
./run.sh          # ou : make run
```
Au premier lancement, `run.sh` entraîne les modèles s'ils manquent, démarre le serveur
sur http://127.0.0.1:8000 et ouvre le navigateur.

## Le cœur : `pipeline/service.py`

Fonction pure du contrat :
```python
predict(competition, home, away, neutral) -> dict
```
Elle déduit le domaine (club vs sélection) de la compétition, reconstruit l'**état
courant** de chaque équipe (forme, Elo, xG via `features.team_state_snapshot`),
fabrique le vecteur de features du match, et renvoie le contrat complet : 1/X/2
calibré, buts attendus, score le plus probable, matrice des scores (0–6), +2,5 buts,
les deux marquent, poids XGBoost du mélange.

Les modèles et les états sont mis en cache (rechargés depuis `data/models/`).

## L'API : `pipeline/api.py` (FastAPI)

| Endpoint | Rôle |
|---|---|
| `GET /api/competitions` | compétitions clubs / sélections |
| `GET /api/teams?domain=club\|international` | équipes (id, nom, Elo) |
| `POST /api/predict` | prédiction complète (corps : competition, home, away, neutral) |
| `GET /api/fixtures` | matchs à venir (Coupe du Monde 2026) |
| `GET /api/simulate` | simulation Monte-Carlo de la CdM 2026 (voir Phase 6) |
| `GET /` | la page web |

## Le frontend : `webapp/`

`index.html` + `style.css` + `app.js` (aucune dépendance, vanilla JS). Deux onglets :
- **Match libre** : sélecteurs compétition / domicile / extérieur / terrain neutre ;
- **Coupe du Monde 2026** : liste des matchs à venir, un clic pré-remplit et prédit.

Affichage : barres de probabilité 1/X/2, buts attendus, score le plus probable,
+2,5 buts, BTTS, et une **heatmap de la matrice des scores**. Le terrain neutre est
coché par défaut pour les matchs de Coupe du Monde.

## Vérification

Stack vérifiée de bout en bout (`tests/test_service_api.py`, sautés si modèles non
entraînés) : contrat de prédiction complet et sommant à 1, effet du terrain neutre,
équipe inconnue rejetée, et les quatre endpoints qui répondent. Tous les chemins HTTP
(page, CSS, JS, API) renvoient 200 avec le bon type de contenu.

## Limite assumée

Les probabilités sont fiables (bien calibrées) mais **ne battent pas le bookmaker**
sur les clubs (cf. `docs/performance.md`). L'app est un excellent estimateur de
probabilités et un simulateur de Coupe du Monde, **pas** une machine à gagner des
paris. La couche qualitative LLM (ajustement borné) reste optionnelle et non activée.
