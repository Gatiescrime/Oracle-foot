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

> Le **mode Complet** (réentraînement) re-télécharge tout, sans plafond de cache.

## 2. Aucune mise à jour ne peut se bloquer

- **Timeout réseau court par source** en Rapide (`QUICK_HTTP_TIMEOUT` = 8 s,
  1 seul essai) : une source lente ne fige pas le job.
- **Timeout global dur** du job (`REFRESH_TIMEOUT_QUICK_S` = 25 s en Rapide,
  300 s en Complet) : au-delà, l'état bascule proprement en **« error »** avec un
  message clair, jamais un sablier infini. Le thread de travail (démon) est
  abandonné sans pouvoir corrompre l'état d'un job plus récent.
- **Bouton Annuler réel** (nouveau) : « Annuler » appelle maintenant
  `POST /api/refresh/cancel`, qui **abandonne immédiatement** le job en cours
  (la génération est incrémentée → le worker devient périmé et ne pourra plus
  réécrire l'état), libère le verrou et repasse à `idle` (« Mise à jour
  annulée »). On peut relancer aussitôt. Avant, « Annuler » ne fermait que la
  fenêtre côté navigateur ; le job continuait en fond.

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
- `pipeline/refresh_job.py` : nouvelle fonction `cancel()` (annulation propre).
- `pipeline/api.py` : nouvel endpoint `POST /api/refresh/cancel`.
- `webapp/app.js` : le bouton « Annuler » appelle le serveur quand un job tourne.

## Tests (tous verts)
- `tests/test_refresh_quick.py` (2) : Rapide ne re-télécharge que la saison en
  cours ; Complet télécharge toutes les saisons.
- `tests/test_refresh_job.py` (+4) : annulation libère le verrou et remet à `idle` ;
  rien à annuler → 409 ; un worker annulé ne réécrit jamais l'état ; endpoint API.
- `tests/test_world_cup_results.py` (2) : parsing joué/à venir + basculement complet.

**Suite complète : 181 tests verts.**

## Critère d'acceptation — atteint
✅ Rapide ne re-télécharge que le strict nécessaire (saison en cours + martj42) → quelques secondes
✅ Aucune mise à jour ne peut se bloquer (timeout par source + global) ; bouton Annuler réel
✅ Après refresh, un match de CdM terminé entre dans l'historique (Elo/features) et quitte « à venir »
✅ Basculement prouvé par un test dédié
