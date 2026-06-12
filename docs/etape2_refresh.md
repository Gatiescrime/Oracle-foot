# Étape 2 — Bouton de mise à jour des données « en temps présent »

## En une phrase

Un bouton **« Mettre à jour les données »** relance, **en tâche de fond**, le
téléchargement des résultats récents et du calendrier, reconstruit les features, et
(en option) réentraîne les modèles — l'app continue de répondre pendant ce temps.

## Fonctionnement

`POST /api/refresh?mode=rapide|complet` réserve le job (verrou anti-double-clic) puis
lance en **`BackgroundTasks`** (réponse HTTP immédiate) :

1. `refresh.refresh(use_cache=False)` — re-télécharge résultats récents + calendrier
   (football-data, understat, martj42), reconstruit la base de façon idempotente ;
2. `features.build_all()` — recalcule **sans fuite** Elo + forme + xG glissant ;
3. mode **« complet »** uniquement : `train.train_all()` réentraîne et réexporte les modèles ;
4. `service.clear_caches()` — l'app sert immédiatement les données fraîches.

Deux modes :
- **Rapide** : données + features, sans réentraînement (les modèles existants resservent) ;
- **Complet** : idem + réentraînement complet.

### Suivi
`GET /api/refresh/status` →
`{state: idle|running|done|error, mode, started_at, finished_at, message, last_updated, running}`.
L'horodatage `last_updated` de la dernière mise à jour réussie est **persisté en base**
(table `app_meta`) et **affiché dans l'UI**.

### Interface
Barre « Données » en haut de l'onglet *Match libre* : « Dernière mise à jour : … »,
sélecteur de mode, bouton, spinner de progression, message de succès/erreur. Pendant
l'exécution, le bouton et le sélecteur sont **désactivés** (anti-double-clic) ; l'UI
**sonde** le statut toutes les 2 s jusqu'à la fin.

## Robustesse (règle d'or : aucun crash)

- Un seul job à la fois (verrou `threading.Lock`) ; un second clic pendant l'exécution
  renvoie **409** côté API et le bouton est de toute façon désactivé côté UI.
- Toute erreur de source réseau est **capturée** : l'état passe à `error` avec un message
  lisible, **sans planter l'app** ; les données précédentes restent en place.
- Anti-fuite préservé : les features sont reconstruites par la même fonction
  chronologique que l'entraînement (aucune information postérieure au match).

## Vérification

- **Chaîne réelle exécutée** : `refresh` → 8 907 matchs clubs, 49 407 sélections,
  70 matchs à venir, xG rattaché ; `features` reconstruites ; `last_updated` persisté ;
  prédiction Man City–Liverpool toujours cohérente (59/19/22) après mise à jour.
- **Tests (`tests/test_refresh_job.py`)** : mode rapide (données+features, pas
  d'entraînement), mode complet (+ entraînement), verrou anti-double-clic, **erreur
  réseau capturée sans crash**, endpoints `POST /api/refresh` (200/409) et
  `GET /api/refresh/status`. **Suite complète : 61 verts.**
