# Phase 3 — Les modèles de prédiction

## En une phrase

Deux modèles complémentaires estiment le **nombre de buts attendus** de chaque
équipe ; on les **mélange**, on en déduit la loi de tous les scores possibles, puis
on **calibre** les probabilités 1/X/2 pour qu'elles soient honnêtes.

## Les deux modèles

### 1. Dixon-Coles (`pipeline/dixon_coles.py`)
Modèle statistique classique du football : chaque équipe a une force d'**attaque**
et de **défense**, plus un **avantage du terrain** (annulé sur terrain neutre, donc
correct pour la Coupe du Monde) et une correction `rho` des petits scores
(0-0, 1-0, 0-1, 1-1). Estimé par maximum de vraisemblance pondéré dans le temps
(les matchs récents comptent plus) et régularisé (L2) pour bien converger même sur
les sélections où les données sont rares.

### 2. XGBoost Poisson (`pipeline/xgb_model.py`)
Deux modèles d'apprentissage (un pour les buts à domicile, un pour l'extérieur) qui
exploitent **27 features** sans fuite (Elo, forme, xG, repos, importance…). Il capte
des interactions non linéaires que Dixon-Coles ne voit pas. Les cotes des bookmakers
sont **volontairement exclues** des entrées (ce serait tricher).

## Le mélange (`pipeline/ensemble.py`)

On combine les deux taux de buts par une **moyenne géométrique** pondérée :
- **clubs** (données riches) → plus de poids à XGBoost (jusqu'à 60 %) ;
- **sélections** (données rares) → plus de poids à Dixon-Coles (XGBoost ≤ 30 %) ;
- le poids est en plus **réduit** quand les deux équipes ont peu de matchs récents.

À partir du couple (buts dom., buts ext.) mélangé, on reconstruit la **matrice des
scores** (Poisson bivarié + correction Dixon-Coles), d'où sortent : 1/X/2, score le
plus probable, +2,5 buts, les deux équipes marquent.

## La calibration (`pipeline/calibration.py`)

Une **régression isotonique par classe** corrige les probabilités pour que « 60 % »
veuille dire « arrive 60 % du temps ». Ajustée sur des prédictions hors échantillon
(jamais sur les données d'entraînement). Résultat : calibration quasi parfaite
(cf. `docs/performance.md`).

## Validation et honnêteté

Tout est jugé par **backtest chronologique** (`pipeline/backtest.py`) : RPS, log-loss,
courbe de calibration, et surtout comparaison au **bookmaker**. Le verdict complet et
sans complaisance est dans `docs/performance.md` — résumé : très bien calibré, proche
du marché sur les clubs mais **ne le bat pas encore**.

## Entraînement et artefacts (`pipeline/train.py`)

```bash
.venv/bin/python -m pipeline.train
```
Réentraîne sur **toutes** les données et exporte dans `data/models/` :
`dixon_coles_{club,intl}.json`, `xgb_{club,intl}_{home,away}.json`,
`calibrator_{club,intl}.json`, `meta_{club,intl}.json`. C'est ce que charge le service
de prédiction (Phase 4).

## Tests
`tests/test_metrics.py` (6) et `tests/test_ensemble.py` (6) : RPS (cas connus,
pénalité d'ordre), dévig des cotes, calibration, bornes du mélange, matrice de score
normalisée, contrat de sortie complet, effet du terrain neutre.
