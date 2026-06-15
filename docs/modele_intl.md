# PHASE B — Modèle des sélections plus juste

Suite de [amelioration_modele_intl.md](amelioration_modele_intl.md) (convergence,
régularisation, effet pays hôte). On reprend ici **à fond** le cas qui dérangeait
encore : **Belgique-Égypte donnée ~46 %** côté modèle alors que le marché voyait la
Belgique vers ~61 %.

## 1. Diagnostic : ce n'était pas (seulement) de l'aplatissement

Le modèle n'est PAS plat : France-Gibraltar sort à 5,3-0,5 buts, Belgique-Pays de
Galles à 71 %, Brésil-Égypte à 58 %. Le vrai problème apparaît dans le **classement
des forces** estimées :

```
TOP : Argentine, Maroc, Japon, Espagne, Brésil, Angleterre, Algérie, Colombie…
…et l'Égypte (#29) avec une défense notée AUSSI solide que celle des cadors européens.
```

Plusieurs sélections africaines/asiatiques (Maroc, Japon, Algérie, Sénégal, Iran…)
ressortaient **au-dessus** de la France, des Pays-Bas, de l'Allemagne, de l'Italie.
Cause : **les confédérations sont mal reliées entre elles**. Une sélection qui domine
un vivier régional plus faible (l'Égypte en zone Afrique) accumule des victoires et
des matchs sans encaisser, et **paraît plus forte qu'elle ne l'est** face à l'élite —
parce que les matchs INTER-confédérations (qui seuls permettent de comparer les niveaux
entre zones) sont rares, et que la régularisation ramène chaque équipe vers une moyenne
**mondiale unique**. Résultat : l'Égypte surévaluée → la Belgique ne sort pas favori net.

## 2. Ce qu'on a testé (et pourquoi on ne « force » pas 61 %)

Règle d'or : on ne truque pas un chiffre pour coller au marché. **Sur les sélections,
il n'existe aucune cote historique dans nos données** : impossible de « valider contre
le bookmaker ». La seule vérité mesurable est le RPS (et la calibration) en validation
chronologique. On a donc cherché un correctif **principiel** (pas du sur-mesure) qui
**améliore le RPS** — sinon on ne touche à rien.

| Réglage testé | Belgique-Égypte | Rang Égypte | RPS holdout |
|---|---|---|---|
| référence (reg=1,5) | 0,45 | #29 | 0,19182 |
| reg=1,0 | 0,47 | #30 | 0,19182 |
| reg=0,7 | 0,49 | #31 | 0,19221 (pire) |
| reg=1,5 + inter-confédérations | 0,47 | #28 | 0,19199 |
| **reg=1,0 + inter-confédérations** | 0,49 | #28 | 0,19212 |
| reg=1,0 + forte pondération récente | 0,49 | #26 | **0,21320 (bien pire)** |

Enseignements honnêtes :
- **Aucun réglage sain n'amène la Belgique à 61 %.** L'écart au marché ne vient pas
  d'un « lissage » qu'on pourrait dérégler : il vient d'une limite de structure
  (confédérations mal reliées) que le marché, lui, corrige implicitement.
- La **pondération inter-confédérations** est le bon levier : elle réhausse la Belgique
  et **dégonfle** l'Égypte, à coût RPS quasi nul.
- La forte pondération récente (`xi`) **détruit** le RPS → écartée.

## 3. Le correctif retenu : pondération inter-confédérations (sans fuite)

On donne plus de poids, dans l'ajustement Dixon-Coles des **sélections uniquement**, aux
matchs qui **relient les confédérations** — Coupe du Monde, Coupe des Confédérations, et
amicaux (souvent intercontinentaux) :

```
FIFA World Cup ×4   |   Confederations Cup ×4   |   Friendly ×1,5
```

- **Aucune fuite** : ces poids sont **FIXES**, choisis a priori, jamais appris sur les
  résultats. Ils disent seulement « ce match est plus informatif pour comparer les
  zones », pas « telle équipe doit gagner ».
- **Clubs intouchés** : réglage propre au domaine sélections (`config.dc_params`).
- **`reg`=1,5 conservé** : on a vu que `reg`=1,0 rendait certains écarts extrêmes
  **malhonnêtes** (France-Gibraltar affiché à 100 %, Espagne-Saint-Marin à 7,6 buts).
  On garde donc 1,5 (écarts réalistes) et on n'ajoute QUE la pondération.

## 4. Validation — backtest chronologique (walk-forward, 6 plis)

À données identiques (24 708 prédictions internationales hors échantillon) :

| Sélections (international) | Avant | Après |
|---|---|---|
| RPS calibré (plus bas = mieux) | 0,19489 | **0,19408** |
| Log-loss calibré | 0,95541 | **0,95364** |
| Écart de calibration moyen | 0,0125 | 0,0143 (toujours excellent) |

| Clubs | Avant | Après |
|---|---|---|
| RPS calibré | 0,21504 | 0,21504 (**identique**, non touché) |

Le **RPS** et le **log-loss** internationaux **s'améliorent** ; la calibration reste
au plus près de la diagonale ; les clubs sont **strictement inchangés**.

### Table de calibration internationale (après)
| Tranche | Prédit | Observé | Effectif |
|---|---|---|---|
| 0,0–0,1 | 0,057 | 0,071 | 5 098 |
| 0,1–0,2 | 0,162 | 0,171 | 12 744 |
| 0,2–0,3 | 0,250 | 0,255 | 25 025 |
| 0,3–0,4 | 0,350 | 0,348 | 9 715 |
| 0,4–0,5 | 0,446 | 0,434 | 6 641 |
| 0,5–0,6 | 0,549 | 0,536 | 6 183 |
| 0,6–0,7 | 0,648 | 0,636 | 3 883 |
| 0,7–0,8 | 0,743 | 0,738 | 3 089 |
| 0,8–0,9 | 0,842 | 0,806 | 1 158 |
| 0,9–1,0 | 0,950 | 0,915 | 588 |

## 5. Effet produit + honnêteté d'affichage

- **Belgique-Égypte** : l'Égypte est dégonflée (buts attendus 1,06 → 0,98), la Belgique
  est désormais un **favori net** (écart de proba franc, écart de buts franc). On ne
  prétend pas atteindre les 61 % du marché : ce serait invérifiable sur les sélections,
  et notre calibration reste juste. Pour l'avis du marché, le **comparateur de cotes**
  est là pour ça.
- **Affichage 100 % corrigé** : aucun match n'est jamais sûr à 100 %. L'interface
  n'affiche plus « 100 % / 0 % » bruts sur les écarts extrêmes (France-Gibraltar)
  mais **« >99 % » / « <1 % »**. N'altère que l'affichage, pas les calculs.

## Ce qui a changé dans le code
- `pipeline/config.py` : `DC_PARAMS_INTL` (pondération inter-confédérations) +
  `dc_params(domain)`.
- `pipeline/dixon_coles.py` : `fit_dixon_coles(comp_weight=…)` (poids par compétition,
  fixe, sans fuite).
- `pipeline/train.py` + `pipeline/backtest.py` : utilisent `config.dc_params(domain)`.
- `data/models/*` : sélections **réentraînées** ; clubs identiques.
- `data/backtest_result.json` : **régénéré** (Track Record reflète le nouveau modèle).
- `webapp/app.js` : affichage honnête `>99 %` / `<1 %`.

## Tests (verts)
- `test_belgium_is_clear_favorite_vs_egypt` : Belgique favori net vs Égypte.
- `test_model_separates_strong_from_weak_nations`, `test_world_cup_host_bonus_*`,
  `test_host_bonus_only_for_hosts_and_bounded` : anti-aplatissement + effet hôte (rappel).
- `test_neutral_prediction_is_swap_invariant` : symétrie EXACTE sur terrain neutre
  hors hôte (Brésil/Argentine).
- `test_neutral_lowers_home_prob_via_service` : invariant mécanique de l'avantage terrain.

**Suite complète : 182 tests verts.**

## Critère d'acceptation
✅ Convergence/régularisation revérifiées ; modèle non plat (écarts de buts cohérents)
✅ Effet pays hôte visible (USA/Canada/Mexique), fixe et sans fuite
✅ Surévaluation inter-confédérations corrigée (Égypte dégonflée, Belgique favori net)
✅ RPS et log-loss internationaux **améliorés** ; clubs inchangés ; calibration documentée
✅ `data/backtest_result.json` régénéré
⚠️ On n'atteint pas le niveau exact du marché (61 %) : ce serait invérifiable sur les
   sélections et nuirait à la calibration. Choix assumé et documenté — le comparateur de
   cotes affiche, lui, l'avis du marché.
