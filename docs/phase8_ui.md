# Phase 8 — Refonte de l'interface web

## En une phrase

Une interface **plus agréable, lisible, responsive et accessible**, toujours en
**HTML/CSS/JS pur** (aucune chaîne de build), pour que l'empaquetage de la Phase 10
reste simple.

## Choix techniques

- **Zéro dépendance, zéro build** : `webapp/index.html` + `style.css` + `app.js`. Servi
  tel quel par FastAPI. Le graphique de simulation est dessiné en CSS (barres), pas de
  librairie de charts.
- **Tout en français**, ton sobre.
- **Responsive** : grilles qui passent en une colonne sous 560 px, aucune barre de
  défilement horizontale sur mobile (vérifié à 375 px).
- **Accessibilité** : lien d'évitement, `:focus-visible` net partout, onglets en
  `role="tablist"/tab/tabpanel` avec `aria-selected`, régions d'erreur `role="alert"`,
  régions live `aria-live`, libellés associés aux champs, fixtures focусables au clavier
  (Entrée/Espace), respect de `prefers-reduced-motion`.

## Ce que contient l'écran

### Onglet « Match libre »
- **Recherche/autocomplétion** de la compétition et des deux équipes via `<input>` +
  `<datalist>` (l'Elo s'affiche en indication). Bouton **⇄** pour inverser domicile /
  extérieur, bascule **terrain neutre** (cochée d'office pour la Coupe du Monde).
- **Carte de prédiction soignée** :
  - barre **1 / X / 2** segmentée + trois pourcentages colorés ;
  - **buts attendus** de chaque équipe et **score le plus probable** mis en avant ;
  - **jauges** « plus de 2,5 buts » et « les deux marquent » ;
  - repli `<details>` avec le poids du modèle XGBoost.
- **Heatmap** de la matrice des scores : échelle de couleur + **survol** affichant la
  probabilité de chaque score exact, avec une légende.
- **Panneau « Actualité »** (couche qualitative) à trois états explicites :
  - **désactivée** → message clair « prédiction issue du seul socle statistique » ;
  - **active avec faits** → multiplicateurs appliqués, facteurs, **faits datés + liens
    de source cliquables**, niveau de confiance et origine ;
  - **active sans actu fiable** → message « aucun ajustement appliqué ».

### Onglet « Coupe du Monde 2026 »
- **Liste des vrais matchs à venir** (endpoint `/api/fixtures`) ; un clic pré-remplit le
  formulaire et lance la prédiction.
- **Bouton de simulation** (endpoint `/api/simulate`) avec un **graphique des
  probabilités de titre** (top 12 en barres) et le **tableau complet des 48 équipes**
  dans un repli.

### États de chargement et d'erreur
Partout : spinner « Calcul des probabilités… », « Chargement du calendrier… »,
« Simulation en cours… » ; messages d'erreur explicites en français (champs invalides,
endpoint indisponible). Le bouton se désactive pendant le calcul.

## Endpoints câblés

`/api/competitions`, `/api/teams?domain=`, `POST /api/predict`, `/api/fixtures`,
`/api/simulate`. Le service renvoie désormais `qualitative_enabled` pour que l'UI sache
afficher l'état de la couche d'actualité.

## Vérification (critère d'acceptation)

- L'app tourne ; **tous les endpoints existants sont câblés**.
- Prédiction de bout en bout vérifiée (Man City–Liverpool : 59/19/22, score 2–1, heatmap
  8×8, jauges, panneau actualité « désactivée » car couche off par défaut).
- Onglet Coupe du Monde : 70 matchs listés, simulation → graphique 12 favoris +
  tableau 48 équipes (Argentine en tête).
- **Desktop et mobile** : rendu propre, une colonne sous 560 px, pas de débordement
  horizontal ; aucune erreur console.
- Suite de tests : **50 verts** (le contrat de prédiction inclut maintenant
  `qualitative_enabled`).
