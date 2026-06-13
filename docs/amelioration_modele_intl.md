# Consolidation — Étape 2 : fiabiliser le modèle sur les sélections

## Le point de départ

Le modèle Dixon-Coles des **sélections** (Coupe du Monde incluse) renvoyait
`convergé = False` : il semblait « mal entraîné ». En réalité deux choses se
mélangeaient — un faux problème et un vrai.

## Ce qu'on a trouvé

### 1. La « non-convergence » était un faux signal
L'optimiseur (L-BFGS-B) s'arrêtait non pas parce qu'il avait fini, mais parce
qu'il **épuisait son budget d'évaluations** (`maxfun`). Avec ~370 paramètres
(184 sélections actives × attaque/défense) et un gradient calculé
numériquement, chaque itération coûte des centaines d'évaluations : le budget
de l'ancien réglage (`max_iter`=500) était dépassé **avant** d'atteindre
l'optimum. Diagnostic mesuré :

```
maxiter=500  : success=False  (STOP: EVALUATIONS EXCEEDS LIMIT)  — faux échec
maxiter=2000 : success=True   (CONVERGENCE: …) à 142 itérations  — vraie réussite
```

→ **Correctif : `max_iter`=2000**, budget suffisant pour une vraie convergence.

### 2. Le vrai problème : un léger surajustement
Une fois la convergence atteinte, la régularisation L2 trop faible
(`reg`=0,05) laissait le modèle surajuster : il produisait des espérances de
buts **absurdes** sur terrain neutre, par exemple

```
France – Gibraltar (neutre)  →  8,2 buts attendus pour la France
```

→ **Correctif : `reg`=1,5** (régularisation L2 plus ferme sur attaque/défense).
La même affiche tombe à **5,3 buts attendus** (toujours large favorite à 97 %,
mais réaliste), et la discrimination entre équipes fortes et faibles est
**préservée** (France–Brésil reste équilibré ~33 % / 45 %).

## Résultats — backtest chronologique (walk-forward, 6 plis)

Comparaison à données identiques (24 703 prédictions internationales hors
échantillon, 4 454 clubs), **avant** = ancien réglage, **après** = nouveau.

| Sélections (international) | Avant | Après |
|---|---|---|
| Convergence Dixon-Coles | ❌ faux échec | ✅ vraie |
| RPS calibré (plus bas = mieux) | 0,1970 | **0,1949** |
| Log-loss calibré | 0,9637 | **0,9554** |
| Écart de calibration moyen | 0,0241 | **0,0125** (≈ ÷2) |

| Clubs | Avant | Après |
|---|---|---|
| RPS calibré | 0,2148 | 0,2150 (stable) |
| Log-loss calibré | 1,0567 | 1,0582 (stable) |

La **calibration internationale** s'améliore nettement (l'écart moyen entre
probabilité annoncée et fréquence observée est divisé par deux) ; le **RPS**
international baisse ; les **clubs** restent stables (ils convergeaient déjà).

### Table de calibration internationale (après)
Probabilité prédite vs fréquence réellement observée — au plus près de la
diagonale = mieux.

| Tranche | Prédit moyen | Observé | Effectif |
|---|---|---|---|
| 0,0–0,1 | 0,059 | 0,068 | 4 916 |
| 0,1–0,2 | 0,160 | 0,168 | 11 717 |
| 0,2–0,3 | 0,247 | 0,253 | 25 833 |
| 0,3–0,4 | 0,349 | 0,348 | 10 160 |
| 0,4–0,5 | 0,448 | 0,435 | 6 864 |
| 0,5–0,6 | 0,549 | 0,538 | 5 706 |
| 0,6–0,7 | 0,645 | 0,626 | 4 496 |
| 0,7–0,8 | 0,742 | 0,742 | 2 713 |
| 0,8–0,9 | 0,842 | 0,812 | 1 127 |
| 0,9–1,0 | 0,947 | 0,919 | 577 |

## Terrain neutre (Coupe du Monde) — vérifié
L'avantage du terrain (`home_adv`) n'entre **pas** dans le calcul sur terrain
neutre : il est purement retiré du lambda domicile. Confirmé par un test dédié
(`test_neutral_ground_has_no_home_advantage`) : sur neutre, λ_domicile est
exactement divisé par `exp(home_adv)`. Côté produit, France–Brésil en neutre
reste un quasi-pile-ou-face, comme attendu.

## Ce qui a changé dans le code
- `pipeline/dixon_coles.py` : valeurs par défaut `reg` 0,05 → **1,5**,
  `max_iter` 500 → **2000** (+ note de calibrage dans la docstring).
- `data/models/*` : modèles **réentraînés** avec ces réglages
  (sélections : `home_adv`=0,274, convergé=True ; clubs : 0,193, convergé=True).
- `data/backtest_result.json` : **régénéré** (le fichier versionné était périmé,
  produit par une version antérieure du code ; il est maintenant cohérent avec
  le modèle déployé).

## Tests
`tests/test_dixon_coles.py` (3 tests, verts) :
- `test_fit_converges_with_many_teams` : sur un championnat synthétique à
  40 équipes, l'estimation **converge** vraiment et retrouve l'avantage terrain ;
- `test_neutral_ground_has_no_home_advantage` : terrain neutre ⇒ aucun avantage ;
- `test_inactive_teams_treated_as_average` : une équipe quasi absente reste
  « moyenne » (prédiction quand même possible).

**Suite complète : 162 tests verts.**

## Critère d'acceptation — atteint
✅ Convergence réellement atteinte (et non plus un faux échec)
✅ Calibration internationale nettement meilleure (écart moyen divisé par ~2)
✅ RPS international amélioré ; RPS clubs stable
✅ Avantage terrain nul sur terrain neutre confirmé (test dédié)
✅ `data/backtest_result.json` régénéré et cohérent avec le modèle déployé
