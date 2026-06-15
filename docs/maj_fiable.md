# PHASE A — Mise à jour fiable + résultats de la Coupe du Monde

Objectif : un bouton « Mettre à jour » **rapide**, qui ne peut **jamais rester
bloqué**, et qui fait **basculer automatiquement** les matchs de Coupe du Monde
terminés depuis le calendrier « à venir » vers l'historique (où ils nourrissent
l'Elo, les features et donc les prédictions suivantes).

## 1. Mode Rapide vraiment rapide (cible < ~15 s)

Avant, le mode Rapide re-téléchargeait **25 fichiers** football-data
(5 ligues × 5 saisons) à chaque clic. Or **seule la saison en cours** (2025/26)
gagne de nouveaux résultats ; les saisons passées sont **immuables**.

Désormais, en Rapide :
- on re-télécharge **uniquement la saison en cours** (5 fichiers, un par ligue) ;
- les **saisons passées** sont servies depuis le **cache disque** (TTL long,
  `QUICK_HEAVY_TTL_HOURS` = 24 h) — aucun appel réseau ;
- les sources lentes (xG / joueurs understat) restaient déjà en cache (TTL 24 h) ;
- martj42 (sélections) = **un seul** CSV, re-téléchargé (léger).

L'historique complet reste intact : toutes les saisons sont concaténées, seules
les **sources réseau** changent. Résultat typique : quelques secondes.

> Le **mode Complet** réutilise lui aussi le cache des sources lentes (voir §2 bis) :
> il ne re-télécharge que le léger récent, puis **réentraîne** les modèles.

## 2. Aucune mise à jour ne peut se bloquer — budgets de temps SÉPARÉS

- **Timeout réseau court par source** (`QUICK_HTTP_TIMEOUT` = 8 s, 1 seul essai)
  pour la phase données des **deux** modes : une source lente ne fige pas le job.
- **Deux budgets distincts** (au-delà : état **« error »** propre, jamais de sablier
  infini) :
  - **phase DONNÉES** (téléchargement léger + features) : `REFRESH_DATA_TIMEOUT_S`
    = **60 s** ;
  - **phase RÉENTRAÎNEMENT** (mode complet) : `REFRESH_TRAIN_TIMEOUT_S` = **480 s
    (8 min)**, son **propre** budget. Le réentraînement est légitimement long et
    n'est donc **jamais tué par le timeout réseau** (c'était la cause de l'échec du
    mode Complet).
- **Bouton Annuler réel** : « Annuler » appelle `POST /api/refresh/cancel`, qui
  **abandonne immédiatement** le job en cours — y compris **pendant le
  réentraînement** (la génération est incrémentée → le worker devient périmé, le
  contrôleur le détecte en ≤ 0,5 s et ne réécrit plus l'état), libère le verrou et
  repasse à `idle`. On peut relancer aussitôt.

## 2 bis. Mode Complet : ne plus re-scraper les sources lentes (CORRECTIF)

**Avant**, le mode Complet re-téléchargeait TOUT depuis zéro (understat compris, très
lent) **puis** réentraînait — le tout sous un seul délai global, qu'il dépassait
→ échec. **Désormais**, le mode Complet fait sa phase données **exactement comme le
Rapide** : il ne re-télécharge que la **saison clubs en cours** + les **sélections
martj42** (résultats récents, Coupe du Monde incluse) et **réutilise le cache** des
sources lentes (xG/joueurs understat, TTL 24 h). Il **réentraîne** ensuite sur ces
données, dans son budget dédié. Aucune source lente n'est re-téléchargée inutilement.

## 3. Résultats de la Coupe du Monde pris en compte (basculement)

martj42 fournit **un seul CSV** mêlant historique et calendrier : un match est
« joué » dès que son score est renseigné, « à venir » sinon. À chaque refresh la
base est reconstruite de zéro (`reset_schema`), donc :

- un match de CdM **terminé** (score renseigné) part dans la table `matches`
  (joués) → il alimente l'**Elo** et les **features**, et **influence les
  prédictions suivantes** ;
- il **quitte** la table `fixtures` (à venir) ;
- il **disparaît** de la liste « à venir » du site (`clear_caches()` vide aussi
  le cache des affiches, et la fenêtre filtre déjà les dates passées).

### Vérifié explicitement par un test
`tests/test_world_cup_results.py` reproduit **deux refresh successifs** de la même
affiche **USA – Paraguay**, sans réseau :
1. d'abord **sans score** (date future) → présente dans `fixtures` et dans la
   liste « à venir », absente de `matches` ;
2. puis **avec le score 4-1** (date passée) → présente dans `matches` (4-1),
   **absente** de `fixtures` et de la liste « à venir ».

## Ce qui a changé dans le code
- `pipeline/sources/football_data.py` : `fetch_all(quick=…)` ne re-télécharge que
  la saison courante ; `fetch_league_season` accepte un `ttl_hours`.
- `pipeline/refresh.py` : transmet `quick` à football-data (docstring mise à jour).
- `pipeline/refresh_job.py` : pipeline en **deux phases à budget séparé**
  (`_do_data` / `_do_train` + `_join_cancellable`) ; le mode complet réutilise le
  cache (`quick=True`) puis réentraîne ; `cancel()` réactif (≤ 0,5 s, même en train).
- `pipeline/config.py` : `REFRESH_DATA_TIMEOUT_S` (60 s) + `REFRESH_TRAIN_TIMEOUT_S`
  (480 s) remplacent l'ancien timeout global unique.
- `pipeline/api.py` : endpoint `POST /api/refresh/cancel`.
- `webapp/app.js` : le bouton « Annuler » appelle le serveur quand un job tourne.

## Tests (tous verts)
- `tests/test_refresh_quick.py` (2) : Rapide ne re-télécharge que la saison en cours ;
  `fetch_all(quick=False)` (CLI hors UI) télécharge toutes les saisons.
- `tests/test_refresh_job.py` : modes rapide/complet, **complet réutilise le cache**
  (`quick=True`) **et** réentraîne ; **budget données** dépassé → erreur propre ; un
  **train long n'est pas tué** par le budget données ; **timeout d'entraînement**
  dédié ; **annulation pendant le train** → idle ; verrou, endpoints API.
- `tests/test_world_cup_results.py` (2) : parsing joué/à venir + basculement complet.

**Suite complète : 208 tests verts.**

## Critère d'acceptation — atteint
✅ Rapide ne re-télécharge que le strict nécessaire (saison en cours + martj42) → quelques secondes
✅ **Complet** : réutilise le cache des sources lentes puis réentraîne, **sans dépasser
   le délai** (budgets réseau et entraînement séparés) ; résultats récents (CdM) intégrés
✅ Aucune mise à jour ne peut se bloquer ; bouton Annuler réel, réactif même pendant le train
✅ Après refresh, un match de CdM terminé entre dans l'historique (Elo/features) et quitte « à venir »
✅ Basculement prouvé par un test dédié
