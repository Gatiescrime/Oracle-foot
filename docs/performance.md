# Phase 3 — Performance des modèles (backtest honnête)

## En une phrase

On rejoue l'histoire dans l'ordre du temps (walk-forward, 6 plis, entraînement
uniquement sur le passé) et on mesure trois choses : la qualité de classement
(**RPS**), la **calibration** des probabilités, et la comparaison au **bookmaker**.
Verdict net et sans enjolivure : **le modèle est très bien calibré et proche du
bookmaker sur les clubs, mais ne le bat pas encore** ; sur les sélections il est
solide mais nous n'avons pas de cotes pour l'étalonner contre le marché.

## Comment reproduire

```bash
.venv/bin/python -m pipeline.backtest        # affiche le JSON des métriques
```

Protocole : pour chaque domaine, on trie les matchs par date, on garde 50 % comme
socle d'entraînement initial, puis on prédit les 50 % restants en 6 blocs successifs.
Chaque bloc est prédit par des modèles entraînés **seulement sur les matchs
antérieurs** (Dixon-Coles + XGBoost, mélangés, puis calibrés sur les plis passés).

## Résultats

### Clubs (5 grands championnats)

| Métrique | Valeur | Lecture |
|---|---|---|
| Prédictions hors échantillon | 4 454 | — |
| **RPS modèle (calibré)** | **0,2007** | plus bas = mieux |
| RPS modèle (brut, non calibré) | 0,2002 | la calibration ne change quasi rien ici |
| **RPS bookmaker** (sous-ensemble avec cotes) | **0,1925** | l'étalon à battre |
| RPS modèle sur ce même sous-ensemble | 0,1992 | **~3,5 % au-dessus du bookmaker** |
| Log-loss (calibré) | 1,005 | — |
| Value betting (edge > 5 %, mise plate) | **ROI −7,6 %** | 3 694 paris, −282 u. : on perd |

**Conclusion clubs, sans détour :** le bookmaker reste meilleur (RPS 0,1925 vs 0,1992).
La stratégie de paris à la valeur **perd de l'argent** (−7,6 %). C'est le résultat
attendu et honnête : battre les cotes des cinq grands championnats, très efficientes,
est extrêmement difficile. Le modèle est néanmoins **dans le bon ordre de grandeur**
et **excellemment calibré** (cf. ci-dessous), ce qui en fait une base saine.

### Sélections (matchs internationaux, dont Coupe du Monde)

| Métrique | Valeur | Lecture |
|---|---|---|
| Prédictions hors échantillon | 24 703 | — |
| **RPS modèle (calibré)** | **0,1884** | meilleur que sur les clubs (matchs souvent déséquilibrés) |
| RPS modèle (brut) | 0,1895 | la calibration aide ici |
| Log-loss (calibré) | 0,936 | — |
| RPS bookmaker | — | **pas de cotes internationales dans nos sources** |

**Conclusion sélections :** modèle solide et bien calibré, mais **non comparable au
marché** faute de cotes internationales. Le RPS plus bas qu'en club s'explique : les
matchs de sélection sont souvent plus déséquilibrés (gros écarts de niveau), donc plus
« faciles » à classer.

## Calibration (le point fort)

Quand le modèle annonce X %, l'événement arrive bien ~X % du temps. C'est vérifié
sur les deux domaines (écart prédit/observé < 1 point sur l'essentiel de la masse) :

**Clubs**

| Proba prédite | Prédit moyen | Observé | Effectif |
|---|---|---|---|
| 0,1–0,2 | 0,155 | 0,163 | 1 696 |
| 0,2–0,3 | 0,263 | 0,263 | 4 778 |
| 0,3–0,4 | 0,342 | 0,336 | 2 676 |
| 0,4–0,5 | 0,438 | 0,440 | 1 278 |
| 0,5–0,6 | 0,556 | 0,549 | 1 063 |
| 0,6–0,7 | 0,636 | 0,640 | 672 |
| 0,7–0,8 | 0,742 | 0,748 | 345 |

(les tranches ≥ 0,8 sont rares en club — 141 puis 26 cas — donc bruitées.)

**Sélections**

| Proba prédite | Prédit moyen | Observé | Effectif |
|---|---|---|---|
| 0,2–0,3 | 0,254 | 0,252 | 25 286 |
| 0,4–0,5 | 0,444 | 0,450 | 7 445 |
| 0,6–0,7 | 0,647 | 0,648 | 3 796 |
| 0,8–0,9 | 0,835 | 0,795 | 1 559 |
| 0,9–1,0 | 0,955 | 0,923 | 1 113 |

Aux très fortes probabilités le modèle est un peu **surconfiant** (annonce 0,95 →
arrive 0,92) : marge de progrès connue.

## Ce que ça implique honnêtement

1. **Critère de succès (battre le bookmaker) : pas encore atteint sur les clubs.**
   On est proche mais en dessous. Il ne faut pas vendre ce modèle comme une machine
   à gagner de l'argent : le value betting est négatif sur l'historique.
2. **Le modèle est fiable comme estimateur de probabilités** (calibration quasi
   parfaite) : très utilisable pour *informer* une décision, afficher des probas
   crédibles, simuler une Coupe du Monde.
3. **Pistes d'amélioration** identifiées pour une éventuelle Phase 5 :
   - features de marché (cotes d'ouverture) comme *cible* de distillation plutôt que
     comme entrée — pour se rapprocher de l'efficience du bookmaker sans tricher ;
   - mélange appris (poids DC/XGBoost optimisés par validation) plutôt que fixé ;
   - regularisation de la surconfiance aux extrêmes (calibration de Platt/beta) ;
   - cotes internationales (Pinnacle) pour enfin étalonner les sélections.

## Artefacts entraînés

`python -m pipeline.train` réentraîne sur **toutes** les données et exporte dans
`data/models/` : paramètres Dixon-Coles, régresseurs XGBoost (buts dom/ext), et
calibrateur isotonique, par domaine. C'est ce que charge le service de prédiction
de la Phase 4.
