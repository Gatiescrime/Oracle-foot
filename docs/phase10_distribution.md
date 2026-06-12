# Étape 4 / Phase 10 — Distribution multi-OS

## En une phrase

L'application peut être ouverte facilement **sur n'importe quelle machine, Windows ET
Mac**, par trois chemins complémentaires, sans jamais exposer la clé API ni dépendre du
réseau au démarrage.

## Trois chemins

### Chemin A — Docker + Render (hébergement par défaut)
- `Dockerfile` **multi-étapes** : un étage installe les dépendances, l'étage final
  (python:3.12-slim + `libgomp1` pour XGBoost) ne garde que le nécessaire. La base
  `data/football.db` et les modèles `data/models/` sont **embarqués** → l'app prédit
  hors ligne dès le démarrage. Le serveur écoute sur `0.0.0.0:$PORT` (forme shell pour
  développer la variable que Render fournit).
- `.dockerignore` exclut les secrets (`.env`, `.claude/`), le cache, les tests, la doc.
- `render.yaml` : service web Docker, plan gratuit, `healthCheckPath`. La clé
  **`ANTHROPIC_API_KEY`** est en `sync:false` → saisie à la main dans le dashboard,
  **jamais** lue depuis le dépôt. `QUALITATIVE_LAYER_ENABLED=false` par défaut.

### Chemin B — Exécutable autonome (PyInstaller + GitHub Actions)
- `packaging/launcher.py` : trouve un port libre, démarre uvicorn, ouvre le navigateur
  quand le serveur répond. Aucune installation de Python pour l'utilisateur final.
- `packaging/app.spec` : embarque `webapp/`, `data/models/`, `data/football.db` ;
  `hiddenimports` pour xgboost, sklearn, scipy, statsmodels, uvicorn, anthropic.
  Produit `PredictionFoot.exe` (Windows) et `PredictionFoot.app` (macOS).
- `.github/workflows/build.yml` : **matrice `windows-latest` + `macos-latest`**,
  construit les deux exécutables à chaque tag `v*` (ou manuellement) et les publie en
  artefacts.

### Chemin D — Mode développement local
- `run.sh` (macOS/Linux, déjà présent) et **`run.bat`** (Windows, ajouté) : entraînent
  les modèles au premier lancement si besoin, démarrent le serveur, ouvrent le
  navigateur.

## Données inscriptibles en mode empaqueté

`pipeline/config.py` distingue désormais :
- **ressources en lecture** (`ROOT`) = dossier d'extraction du bundle (`sys._MEIPASS`)
  en exécutable, sinon la racine du dépôt ;
- **données inscriptibles** (`DATA_DIR`) = `~/PredictFoot/data` en exécutable, sinon la
  racine du dépôt.

`config.seed_writable_data()` recopie, au premier lancement de l'exécutable, la base et
les modèles livrés vers le dossier inscriptible. **En développement, les deux chemins
sont identiques → comportement inchangé** (les 69 tests restent verts).

## Décisions verrouillées (appliquées)

- La base `data/football.db` est **versionnée** (retirée de `.gitignore`) : builds
  reproductibles et démarrage hors ligne. Seuls le cache et les logs restent ignorés.
- Hébergement par défaut = **Render**.
- La clé API n'est **jamais** dans le dépôt ni exposée au frontend.

## Vérification

- **Lanceur empaqueté simulé** : `packaging/launcher.py` démarre réellement le serveur
  sur un port libre et sert `/api/competitions` et `/api/scorers` (testé en local).
- **Mode dev intact** : `DATA_DIR == RESOURCE_DATA_DIR`, `seed_writable_data()` est un
  no-op, **suite complète : 69 verts**.
- Docker/Render : fichiers conformes (image multi-étapes, `$PORT`, secrets hors dépôt).
  Le build d'image n'a pas pu être exécuté ici (Docker non installé sur la machine),
  mais la configuration est prête à être déployée.
