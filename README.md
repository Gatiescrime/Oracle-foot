# ⚽ Prédiction Foot — probabilités calibrées

Application de prédiction de matchs de football (5 grands championnats européens +
sélections / Coupe du Monde 2026). Elle donne des **probabilités calibrées** (victoire /
nul / défaite, score le plus probable, plus de 2,5 buts, les deux marquent), des
**buteurs probables**, une **couche actualité** optionnelle (blessures/absences) et une
**simulation de la Coupe du Monde**.

> Probabilités indicatives à but informatif. Ce n'est pas un conseil de pari.

---

## Quelle option choisir ?

| Vous voulez… | Option | Difficulté |
|---|---|---|
| Ouvrir l'app depuis n'importe où, juste une adresse web | **A. Hébergement Render** | ⭐ facile |
| Tout faire tourner sur votre machine via Docker | **B. Docker** | ⭐⭐ moyen |
| Une application à double-cliquer (Windows / Mac), sans rien installer | **C. Exécutable** | ⭐ facile (une fois construit) |
| Développer / modifier le code | **D. Mode développement** | ⭐⭐⭐ technique |

L'application **fonctionne hors ligne** dès le départ : la base de données et les
modèles entraînés sont **livrés avec le projet**. Le bouton « Mettre à jour les
données » va chercher les résultats récents quand vous le souhaitez.

---

## A. Hébergement Render (recommandé)

1. Créez un compte sur [render.com](https://render.com) et poussez ce dépôt sur GitHub.
2. Sur Render : **New > Blueprint**, sélectionnez le dépôt. Le fichier `render.yaml`
   est détecté automatiquement.
3. Dans le dashboard, renseignez la variable **`ANTHROPIC_API_KEY`** (votre clé
   officielle Anthropic) — uniquement si vous voulez activer la couche actualité.
   *Cette clé n'est jamais stockée dans le dépôt.*
4. Render construit l'image et vous donne une adresse `https://…onrender.com`.

La couche actualité est **désactivée par défaut** (`QUALITATIVE_LAYER_ENABLED=false`)
car elle consomme l'API payante. Activez-la dans le dashboard si besoin.

## B. Docker (sur votre machine)

Prérequis : [Docker](https://www.docker.com/) installé.

```bash
docker build -t prediction-foot .
docker run -p 8000:8000 prediction-foot
# puis ouvrir http://127.0.0.1:8000/
```

Pour activer la couche actualité :
```bash
docker run -p 8000:8000 -e QUALITATIVE_LAYER_ENABLED=true \
  -e ANTHROPIC_API_KEY=sk-ant-... prediction-foot
```

## C. Exécutable autonome (Windows .exe / macOS .app)

Aucune installation de Python pour l'utilisateur final.

- **Récupérer un exécutable déjà construit** : poussez un tag `v1.0` (ou lancez le
  workflow manuellement) — GitHub Actions construit automatiquement les versions
  **Windows et macOS** (onglet *Actions* > *Build executables* > *Artifacts*).
- **Construire soi-même** (depuis la racine du dépôt, avec Python installé) :
  ```bash
  pip install -r requirements.txt pyinstaller
  pyinstaller packaging/app.spec
  # résultat dans dist/ : PredictionFoot.exe (Windows) ou PredictionFoot.app (macOS)
  ```

Au lancement, l'app ouvre votre navigateur automatiquement. Les données modifiables
(base mise à jour, cache) sont écrites dans `~/PredictFoot/`.

## D. Mode développement (local)

Prérequis : Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

Puis lancez l'app :
- macOS / Linux : `./run.sh`
- Windows : double-cliquez `run.bat`

Les modèles sont entraînés automatiquement au premier lancement s'ils manquent.

### Couche actualité (optionnelle, payante)

Pour activer les ajustements blessures/absences via recherche web :
créez un fichier `.env` à la racine (jamais versionné) :
```
ANTHROPIC_API_KEY=sk-ant-...
QUALITATIVE_LAYER_ENABLED=true
```
Garde-fous intégrés : réponses mises en cache, nombre de recherches web plafonné par
match, compteur d'appels du jour affiché, ajustement des buts **borné à ±25 %**.

---

## Commandes utiles

```bash
python -m pipeline.refresh            # met à jour la base (avec cache)
python -m pipeline.refresh --no-cache # force le re-téléchargement
python -m pipeline.train              # (ré)entraîne les modèles
python -m pytest                      # lance la suite de tests
```

## Architecture (en bref)

- **Modèle** : Dixon-Coles (Poisson bivarié) + XGBoost, mélange géométrique,
  puis **calibration isotonique** des probabilités 1/X/2.
- **Anti-fuite** : toutes les features sont reconstruites de façon chronologique ;
  aucune information postérieure au match n'entre dans une prédiction.
- **Données** : football-data.co.uk (résultats + cotes), understat (xG et buteurs),
  martj42 (sélections internationales), valeur d'effectif éditable (`data/squad_values.csv`).
- **Features enrichies (P5)** : valeur marchande des effectifs (proxy de niveau),
  Elo séparé en rating **offensif** et **défensif**, et avantage du terrain
  **spécifique à la compétition** (nul en terrain neutre). Gain de RPS/calibration
  modeste mais réel et sans régression — détail dans `docs/phaseP5_donnees.md`.
- **Contexte testé puis écarté (P6)** : qualité fine des tirs, congestion/déplacement
  et météo ont été construits, mesurés proprement (walk-forward), puis **jugés sans
  gain honnête** et **retirés du modèle** — discipline anti-surapprentissage assumée,
  détail dans `docs/phaseP6_contexte.md`.
- **Sécurité** : la clé API n'est **jamais** exposée au frontend ni au dépôt.

## Paris, value et mise (analyse honnête)

Le vrai juge de paix d'un modèle de foot, c'est : **bat-il les bookmakers ?**

- **Backtest de paris** (`pipeline/betting.py`) : on rejoue l'historique de façon
  chronologique, on ne mise que sur les cotes d'**ouverture** (jamais la clôture,
  pour éviter toute fuite) et on mesure rendement, ROI et CLV (*closing line value*).
- **Cotes du marché en entrée** (`pipeline/market_eval.py`) : intégrées comme
  features, elles **améliorent la calibration et le RPS**, mais ne créent **aucun
  avantage exploitable** (rendement négatif, CLV ≈ 0). Désactivées par défaut, car
  les matchs à venir (Coupe du Monde, sélections) n'ont pas de cotes.
- **Recommandation de mise** (`pipeline/staking.py`, `POST /api/stake`) : pour une
  issue et une cote, calcule l'edge, l'espérance et une mise prudente en **Kelly
  fractionné plafonné** (¼ de Kelly, cap 5 % du capital). Sans value, le dit
  clairement et ne conseille rien.

> **Verdict honnête** : le modèle est **bien calibré** mais **ne bat pas le
> marché** sur le 1/X/2 aujourd'hui. L'outil de mise calcule proprement *combien
> miser SI* il y a value — il ne crée pas la value. Aucune promesse de gain.

## Comparateur de cotes en live (line shopping)

À proba égale, mieux vaut encaisser la cote la plus haute. Le panneau **« Meilleures
cotes du marché »** interroge en direct des dizaines de bookmakers et affiche le
**meilleur prix par issue**, croisé avec la proba du modèle pour signaler la **value**.

- **Source** : [the-odds-api.com](https://the-odds-api.com) (palier gratuit). La clé
  vit dans `.env` (`ODDS_API_KEY`), **jamais** dans le frontend ni le dépôt.
- **Cache** (`ODDS_API_TTL_HOURS`, défaut 6 h) : consulter dix affiches du même
  tournoi ne coûte qu'**un seul appel** ; le quota restant est affiché dans l'UI.
- **Repli sans clé** : on retombe sur les **cotes d'ouverture football-data** du
  dernier match connu, étiquetées *« historique, pas un prix live »*.
- **CLV en live (proxy)** : `/api/odds/clv` rapporte le mouvement entre notre premier
  et notre dernier prix capté — un proxy utile, pas la CLV de clôture exacte.
- **Endpoints** : `GET /api/odds/live`, `/api/odds/status`, `/api/odds/clv`.

> Le line shopping **augmente le prix encaissé** quand on parie — réel et mesurable —
> mais ne fabrique pas la value : il aide à **exécuter au mieux** un pari, pas à
> battre le marché.

Documentation détaillée par étape dans le dossier [`docs/`](docs/) — voir
notamment `perf_paris.md` (P1), `phaseP2_marche.md` (P2), `phaseP3_mise.md` (P3),
`phaseP4_cotes_live.md` (P4), `phaseP5_donnees.md` (P5) et
`phaseP6_contexte.md` (P6 — contexte testé puis écarté).
