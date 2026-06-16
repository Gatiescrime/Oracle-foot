# Bilan honnête de la Coupe du Monde (prédit vs réel)

Pour chaque match de la **Coupe du Monde 2026 déjà joué**, on montre ce que le modèle
aurait prédit **avant le coup d'envoi**, face au **résultat réel** — match par match
et en bilan global.

## La règle d'or : zéro fuite

La prédiction d'un match du jour `d` est faite par un modèle (Dixon-Coles + XGBoost +
calibration) **reconstruit uniquement à partir des matchs internationaux STRICTEMENT
antérieurs à `d`**. Jamais le match lui-même, jamais un match postérieur. C'est ce qui
rend la comparaison **honnête** — sinon elle ne vaudrait rien.

- Un seul réajustement **par date** (les matchs d'un même jour partagent le même passé).
- Le calibrateur est ajusté sur les matchs **antérieurs au tout premier match** de la
  CdM (donc avant tous les matchs évalués) : leak-free, et un seul ajustement.
- Vérifié par tests : l'ensemble d'entraînement d'un match **exclut** ce match et tout
  match daté de `d` ou après ; modifier le score d'un match **postérieur** ne change
  **pas** la prédiction pré-match d'un match évalué.

C'est une mesure **walk-forward**, à distinguer du **journal de prédictions live**
(ce que l'utilisateur prédit lui-même, noté au fil de l'eau) : le bilan CdM est
disponible **tout de suite** pour tous les matchs joués.

## Ce qui est affiché

Dans la page **Track record**, un panneau « Coupe du Monde 2026 — bilan prédit vs réel » :

- **Bilan global** : taux de bon pronostic 1X2, RPS moyen, **score moyen prédit vs
  marqué**, et **courbe de calibration** sur la CdM.
- **Match par match** : drapeaux des deux équipes, « **Issue prédite** : Victoire X
  (proba) / Nul » + le **score probable** a–b, en regard de « **Réel** : a–b », et une
  **pastille ✓ juste / ✗ raté**.

> **Sur quoi porte la pastille ?** La pastille **✓ juste / ✗ raté** juge l'**issue**
> 1X2 (bon vainqueur ou nul), **pas** le score exact. C'est pourquoi l'issue prédite
> est affichée explicitement. Un indicateur **séparé 🎯 « score exact »** signale, à
> part, quand le score probable correspond aussi au score réel. Exemple parlant :
> Qatar–Suisse, score 1–1 prédit *et* réel → 🎯 score exact, mais le modèle annonçait
> une victoire suisse alors que c'est un nul → l'issue est **✗ ratée**. Une légende le
> rappelle dans l'interface. (Affichage uniquement : les statistiques — bon pronostic
> 1X2, RPS, score moyen — sont inchangées.)

## Calcul, coût, mise à jour

`pipeline/wc_bilan.py` réutilise l'infrastructure de backtest (`_load`, `_dc_matches`,
`fit_dixon_coles`, `XGBPoissonModel`, le calibrateur de `train`, `EnsemblePredictor`).
Le résultat est **mis en cache** (`data/wc_bilan.json`) : une prédiction pré-match est
**stable** une fois calculée, donc à chaque mise à jour on ne recalcule que les
**nouvelles dates**.

- **CLI** : `python -m pipeline.wc_bilan` (calcule / complète le bilan).
- **Refresh complet** : le bilan est mis à jour automatiquement (incrémental,
  best-effort) à la phase de réentraînement. Une mise à jour *rapide* ne le recalcule
  pas (le bilan reste servi depuis le cache).
- Servi à l'UI via `/api/track-record` (clé `wc`), enrichi des drapeaux.

## Tests (verts)
`tests/test_wc_bilan.py` : anti-fuite (`_train_before` exclut le match et le futur ;
un score futur ne change pas l'ensemble d'entraînement d'un match évalué) ; agrégats
du bilan ; cas vide. **Suite complète : verte.**

## Critère d'acceptation — atteint
✅ Chaque match de CdM joué et en base apparaît avec sa **prédiction pré-match** (1X2,
   buts attendus, score probable) face au **résultat réel** + pastille juste/raté
✅ **Bilan global** : bon pronostic 1X2, RPS moyen, calibration, score moyen prédit vs réel
✅ **Zéro fuite** garanti (modèle reconstruit sur les seuls matchs antérieurs) et testé
✅ Clairement distingué du **journal de prédictions live**
