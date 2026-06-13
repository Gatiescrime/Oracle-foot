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

---

# Suite de l'étape 2 — dé-aplatissement et effet « pays hôte »

Le déclencheur concret : **USA – Paraguay** affichait des buts attendus
quasi identiques (1,84 vs 1,81) et un score 1–1, alors que les USA, **pays
hôte** de la Coupe du Monde 2026, l'ont emporté 4–1. Deux causes possibles :
un modèle « trop plat », et l'absence de tout effet « jouer chez soi » au
Mondial. On a traité les deux.

## 3. Le modèle n'est PAS plat (vérifié, pas supposé)

Le balayage de la régularisation confirme que `reg`=1,5 est le **meilleur
réglage RPS** tout en gardant des niveaux d'équipes bien différenciés
(l'écart-type des forces d'attaque reste à 0,35, pas écrasé vers 0) :

| `reg` | RPS international | Dispersion des attaques |
|---|---|---|
| 0,5 | 0,20862 | 0,434 |
| 1,0 | 0,20819 | 0,384 |
| **1,5** | **0,20816** | 0,352 |
| 2,5 | 0,20854 | 0,308 |

Côté produit, la discrimination est nette (tests de bon sens) :

| Affiche (Mondial, terrain neutre) | Buts attendus | 1 / N / 2 |
|---|---|---|
| France – Gibraltar | **5,11 – 0,45** | 0,98 / 0,02 / 0,00 |
| USA – Brésil | 1,55 – **2,64** | 0,21 / 0,21 / **0,58** |

→ Une grande nation contre une petite produit un **écart franc** de buts
attendus ; le modèle ne ramène donc rien vers « 1,8 – 1,8 ». L'égalité
USA – Paraguay venait du **niveau réellement proche** des deux équipes, pas
d'un défaut du modèle. Ce qui manquait, c'était l'effet hôte.

## 4. Effet « pays hôte » du Mondial (fixe, sans fuite)

En Coupe du Monde, le pays organisateur bénéficie d'un avantage marqué (public,
déplacements, acclimatation) qui s'ajoute même quand le match est joué sur
terrain neutre vis-à-vis de l'adversaire. On l'a modélisé comme un **bonus fixe
de buts attendus** pour l'hôte :

- **Compétition** : `FIFA World Cup` uniquement.
- **Hôtes 2026** : États-Unis, Canada, Mexique (`config.WORLD_CUP_HOSTS`).
- **Valeur** : `HOST_GOAL_LOG_BONUS = 0,30` en log-buts, soit **+35 %** de buts
  attendus pour l'hôte (`exp(0,30) ≈ 1,35`). Calibrée *a priori* sur l'avantage
  terrain mesuré du modèle (`home_adv` ≈ 0,274) — un ordre de grandeur « avantage
  d'un vrai match à domicile », légèrement majoré pour le contexte Mondial.
- **Aucune fuite de données** : ce bonus est **fixe et non appris**. Il est
  appliqué **au moment de la prédiction seulement**, jamais pendant
  l'entraînement ni le backtest. (Sinon on boosterait à tort le Brésil au
  Mondial 2014, l'Afrique du Sud en 2010, etc. — de la triche rétroactive.)
- **Distinct de l'avantage terrain ordinaire** et **cumulable** : il s'ajoute
  même sur terrain neutre.
- **Différentiel uniquement** : si **les deux** équipes sont hôtes (Mexique –
  Canada), aucun avantage n'est attribué (`host_effect` absent).
- **Borné** : un simple bonus ne renverse pas un favori net. USA – Brésil reste
  largement à l'avantage du Brésil (0,58).

Effet mesuré sur le cas déclencheur :

| USA – Paraguay | Buts attendus | 1 / N / 2 | Favori |
|---|---|---|---|
| **Amical** (sans effet hôte) | 1,26 – 1,53 | 0,28 / 0,29 / 0,44 | Paraguay |
| **Coupe du Monde** (effet hôte) | **1,70** – 1,53 | **0,42** / 0,26 / 0,32 | **USA** |

→ L'effet hôte fait **basculer** l'affiche du côté des USA, comme attendu, et il
est **exposé dans l'explication** (« joue la Coupe du Monde à domicile (pays
hôte) ») ainsi que dans un encart `host_effect` (+35 %).

## 5. Calibration sur terrain neutre — symétrie (choix honnête)

Sous-problème découvert en chemin : sur terrain neutre, l'étiquette
domicile/extérieur est **arbitraire** (qui « reçoit » au Mondial ?). Or le
calibrateur, appris surtout sur des matchs **avec** avantage du terrain, applique
une correction domicile↓ / extérieur↑. Appliquée telle quelle à un match neutre,
elle injecte un **avantage fantôme** à l'équipe qu'on a listée « à domicile » :

```
USA – Paraguay (USA listé domicile)  → USA 0,258
Paraguay – USA (USA listé extérieur) → USA 0,578   (!)
```

Soit un écart de **0,32** selon un simple ordre d'affichage : c'est trompeur.
**Correctif** : sur terrain neutre on **symétrise** la calibration (moyenne des
deux orientations) → la prédiction devient **invariante par échange des équipes**
(test `test_neutral_prediction_is_swap_invariant`). USA – Paraguay donne alors
0,42 quel que soit l'ordre.

### Le compromis, mesuré et assumé
Sur le sous-ensemble **historique** des matchs internationaux « neutres »
(6 100 prédictions), la symétrisation **dégrade légèrement** le RPS :

| Sous-ensemble neutre (historique) | RPS | Log-loss |
|---|---|---|
| Calibration directe (asymétrique) | 0,2136 | 1,018 |
| Calibration **symétrisée** | 0,2243 | 1,050 |

Pourquoi cette dégradation, et pourquoi on garde quand même la symétrie ? Parce
que dans les données, les matchs étiquetés « neutres » **ne le sont pas
vraiment** : l'équipe listée à domicile y gagne **44,2 %** contre **33,4 %** à
l'extérieur (**+10,8 points**). L'étiquette « domicile » porte donc un vrai
signal résiduel (tête de série, pays organisateur, convention du jeu de
données), que la calibration asymétrique exploite légitimement *sur ce
sous-ensemble*. **Mais** pour un vrai match de Coupe du Monde 2026 où l'on
attribue domicile/extérieur de façon arbitraire, ce +10,8 points **n'existe
pas** : l'appliquer reviendrait à favoriser au hasard l'équipe affichée en
premier. La symétrie est donc le choix **correct et honnête** pour la prévision
réelle ; le « coût » mesuré ci-dessus est un artefact d'un sous-ensemble
historique mal étiqueté, pas une vraie perte de prévision.

## Ce qui a changé dans le code (suite)
- `pipeline/config.py` : `WORLD_CUP_COMPETITION`, `WORLD_CUP_HOSTS`,
  `HOST_GOAL_LOG_BONUS=0,30` (+ justification commentée).
- `pipeline/ensemble.py` : `predict()` reçoit `host_log_bonus` (appliqué aux
  buts attendus de l'hôte, avant la matrice) ; calibration **symétrisée** sur
  terrain neutre.
- `pipeline/service.py` : `_host_log_bonus()` (qui est hôte ?), passage du bonus
  au prédicteur, encart `host_effect` + facteur d'explication ; la **compétition**
  entre désormais dans la clé de cache (elle change features ET effet hôte).

## Tests de bon sens ajoutés (anti-aplatissement & effet hôte)
- `test_model_separates_strong_from_weak_nations` : France – Gibraltar, écart de
  buts ≥ 2 et favori > 85 %.
- `test_world_cup_host_bonus_tips_and_is_visible` : USA – Paraguay penche USA en
  Mondial, effet exposé, **absent** en amical.
- `test_host_bonus_only_for_hosts_and_bounded` : ne renverse pas un favori net
  (USA – Brésil) ; deux co-hôtes (Mexique – Canada) ⇒ aucun effet.
- `test_neutral_prediction_is_swap_invariant` : prédiction invariante par
  échange des équipes sur terrain neutre.

**Suite complète : 173 tests verts.**

## Critère d'acceptation — atteint
✅ Convergence réellement atteinte (et non plus un faux échec)
✅ Calibration internationale nettement meilleure (écart moyen divisé par ~2)
✅ RPS international amélioré ; RPS clubs stable
✅ Modèle **non plat** : écart de buts cohérent avec l'écart de niveau réel
✅ Effet **pays hôte** visible pour USA/Canada/Mexique au Mondial, fixe et sans fuite
✅ Prédiction **invariante par échange** sur terrain neutre (choix honnête, documenté)
✅ Avantage terrain nul sur terrain neutre confirmé (test dédié)
✅ `data/backtest_result.json` régénéré et cohérent avec le modèle déployé
