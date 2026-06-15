# Apprentissage — PHASE 3 : boucle de correction (bornée, validée, réversible)

À partir du **journal des prédictions** (erreurs réellement réalisées), le site peut
apprendre une **correction** appliquée aux prochaines prédictions — sous des
garde-fous stricts, pour ne **jamais** rendre le modèle pire.

## La correction : une « température » bornée, par domaine

Un seul paramètre **T** par domaine (clubs / sélections) :

- **T < 1** affûte les probabilités (plus de confiance au favori) — corrige une
  **sous-estimation des favoris** / une sous-confiance ;
- **T > 1** les aplatit (corrige une sur-confiance) ;
- **T = 1** ne change rien (neutre).

Un seul paramètre ⇒ **robuste** même avec peu de données, et **strictement borné**
(`T ∈ [0,70 ; 1,40]`, à l'apprentissage ET à l'application) ⇒ la correction ne peut
pas dérailler.

## Les garde-fous (règles d'or)

1. **Validation obligatoire hors échantillon.** On découpe le journal dans le
   **temps** : T est ajusté sur le passé, puis on ne le **conserve que s'il améliore
   le RPS** sur la tranche de validation finale. Sinon, **T = 1** (aucune correction).
2. **Anti-boucle.** T est recalculé sur **l'ensemble des erreurs accumulées** (jamais
   en réaction à un seul match), et **à partir des probabilités BRUTES** du modèle :
   la correction d'affichage est appliquée **après** la journalisation, donc jamais
   réinjectée dans son propre apprentissage.
3. **Données minimales.** En dessous de 100 prédictions réglées par domaine, aucune
   correction n'est tentée (pas assez de recul).
4. **Réversible.** Un drapeau (`config.CORRECTION_ENABLED`) et l'état `enabled` de
   chaque domaine la désactivent ; un fichier de correction absent/corrompu ne casse
   rien (prédiction inchangée).
5. **Le réentraînement complet continue en parallèle** (il intègre les nouveaux
   résultats). Cette couche n'est qu'un ajustement fin de calibration, validé.

## Quand est-elle recalculée ?

À **chaque mise à jour des données**, après le règlement des prédictions :
`correction.refit_from_journal()` relit tout le journal, apprend+valide T par domaine
et persiste l'état (`data/models/correction.json`). Best-effort : un souci
d'apprentissage ne fait jamais échouer la mise à jour.

## Validation sur backtest réel — « ne fait pas pire »

Sur **24 708** prédictions internationales calibrées hors échantillon (walk-forward) :

| | RPS global | Écart de calibration (ECE) |
|---|---|---|
| Modèle seul (T=1) | 0,19408 | 0,0143 |
| Modèle + correction | **0,19408** | **0,0143** |

La correction apprise n'est **pas conservée** ici : sur la tranche de validation, la
température n'améliore pas le RPS (0,18658 → 0,18699). Le garde-fou la **refuse** donc,
et le résultat est **strictement identique** au modèle. C'est le comportement voulu :
le modèle est déjà bien calibré (grâce à la calibration isotonique), donc aucune
correction supplémentaire n'est justifiée — et le système le **détecte tout seul**.

> Autrement dit : aujourd'hui, la boucle est en place mais **inactive** (rien à
> corriger). Elle ne s'activera que si, au fil des vrais résultats (Coupe du Monde…),
> un biais systématique apparaît ET qu'une correction bornée l'améliore en validation.

Côté tests, on prouve l'autre sens : sur des prédictions **volontairement
sous-confiantes**, la boucle apprend un **T < 1**, le **conserve** (amélioration
validée hors échantillon) et reste **borné** ; sur des prédictions bien calibrées,
elle ne conserve rien.

## Honnêteté & limites (Phase 4)

À dire clairement, sans survendre :

- La correction **améliore la calibration** (l'accord entre « X % annoncé » et « X %
  observé »), elle **ne garantit aucun gain**. Mieux calibré ≠ rentable : sur les
  clubs, le bookmaker reste devant (cf. Track record).
- Elle est **bornée** (T ∈ [0,70 ; 1,40]) et **validée hors échantillon** :
  c'est précisément ce qui **évite le surapprentissage**. Si elle n'aide pas en
  validation, elle **n'est pas appliquée** (T=1). Aujourd'hui, sur nos données, elle
  est **inactive** (le modèle est déjà bien calibré) — et c'est honnête de le dire.
- Une « value » ou une prédiction reste une **estimation**, jamais une certitude. Le
  site n'incite à aucune mise garantie.

## Persistance & hébergement (Phase 4)

La mémoire (journal des prédictions + correction) vit dans **`football.db`** : c'est
un simple fichier.

- **En local** : le fichier persiste sur le disque → la mémoire **survit aux
  redémarrages** de l'application **et** aux mises à jour des données (la table
  `predictions_log` est dans le schéma persistant, donc **non effacée** par un
  refresh). Vérifié par un test dédié.
- **Sur Render (offre gratuite)** : le disque est **éphémère** — il est **réinitialisé
  à chaque redéploiement**. La mémoire ne survivrait donc PAS à un redéploiement. Pour
  une mémoire durable en ligne, deux options (aucune imposée) :
  1. **La plus simple — un disque persistant** : attacher un *Persistent Disk* au
     service (offre payante de Render), le monter (ex. `/var/data`) et pointer l'app
     dessus avec la variable d'environnement **`PREDICT_FOOT_DATA_DIR=/var/data`**.
     L'app **amorce automatiquement** ce volume (base + modèles livrés) au premier
     démarrage, puis y accumule le journal qui **survit aux redéploiements**.
  2. **Une base externe** (ex. Postgres managé) : plus robuste mais demande une
     migration du stockage — surdimensionné pour l'usage actuel.
- **Recommandation honnête** : pour l'apprentissage par l'expérience, l'**usage local**
  (ou un petit disque persistant) suffit largement ; inutile de complexifier tant que
  le volume de prédictions reste modeste.

## Ce qui a changé dans le code
- `pipeline/config.py` : `PREDICT_FOOT_DATA_DIR` (redirige les données inscriptibles vers
  un volume persistant) + amorçage automatique idempotent du volume.
- `pipeline/correction.py` (nouveau) : `temper`, `fit_domain` (apprentissage + validation
  chronologique), `refit_from_journal`, `apply_to_pred`, persistance.
- `pipeline/service.py` : applique la correction APRÈS journalisation des probas brutes.
- `pipeline/refresh_job.py` : recalcule la correction après chaque règlement (best-effort).
- `pipeline/config.py` : `CORRECTION_ENABLED` (réversible).

## Tests (verts)
`tests/test_correction.py` : détection + correction d'une sous-confiance (T<1 conservé,
amélioration hors échantillon) ; aucune correction si déjà bien calibré ; **rejet** d'un
T qui n'aide pas la validation ; **bornes** respectées même sur biais extrême ; données
insuffisantes ⇒ désactivée ; **réversibilité** (drapeau / T=1) ; **idempotence** et
**robustesse** (un état corrompu ne casse jamais une prédiction). Côté journal,
`test_journal_survives_refresh_and_restart` prouve que la mémoire **survit à un refresh
et à un redémarrage**. **Suite : 225 tests verts.**

## Critère d'acceptation — atteint
✅ Détection de biais systématiques à partir du journal (sous/sur-confiance)
✅ Correction **bornée** [0,70 ; 1,40] et **validée hors échantillon** (sinon non appliquée)
✅ Sur backtest : modèle + correction **ne fait pas pire** (identique ici, le garde-fou refuse)
✅ **Anti-boucle** (ensemble des erreurs accumulées, probas brutes) et **réversible**
✅ Réentraînement existant conservé en parallèle
✅ **Honnêteté** : améliore la calibration, **pas un gain garanti** ; bornée/validée (anti-surapprentissage)
✅ **Persistance** : mémoire durable en local (survit refresh + redémarrage, testé) ;
   disque Render éphémère documenté + option simple (`PREDICT_FOOT_DATA_DIR` → volume persistant)
