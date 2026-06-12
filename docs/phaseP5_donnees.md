# Phase P5 — Enrichissement des données : valeur d'effectif + Elo offensif/défensif

## L'idée, en une phrase

On donne au modèle **trois nouveaux signaux pré-match** — combien vaut l'effectif,
qui attaque/défend bien (séparément), et **à quel point jouer à domicile compte selon
la compétition** — puis on mesure honnêtement si ça aide.

## Ce qu'on a ajouté

### 1. Valeur marchande des effectifs (proxy de niveau)

- Source : un **CSV versionné et éditable** (`data/squad_values.csv`, colonnes
  `team` + `value_meur`, en millions d'euros, ordre de grandeur type Transfermarkt).
  Une URL optionnelle (`SQUAD_VALUE_URL`) permet un rafraîchissement, jamais requis.
- Aligné sur nos noms canoniques par **appariement** (exact normalisé puis flou,
  réutilise `names`). Une équipe absente du fichier → feature **NaN** (gérée
  nativement par XGBoost). Les sélections nationales n'ont pas de valeur d'effectif :
  le signal y est donc inerte (NaN), c'est attendu.
- Features : `home_squad_value`, `away_squad_value`, `squad_value_logratio`
  (`log(valeur_dom / valeur_ext)`, NaN si une valeur manque).
- **Pourquoi** : la valeur d'effectif est un proxy de niveau **structurel**,
  indépendant de la forme. Utile en début de saison ou pour un promu, là où l'Elo
  et la forme manquent de recul.

### 2. Elo séparé en OFFENSIF et DÉFENSIF

Au lieu d'un rating Elo unique (« niveau global »), on suit **deux ratings par
équipe** : un offensif (capacité à marquer) et un défensif (capacité à ne pas
encaisser). Modèle de buts à lien log mis à jour match par match (gradient en ligne,
dans l'esprit des forces d'attaque/défense de Dixon-Coles) :

```
buts_attendus_dom = moyenne × exp((off_dom − déf_ext + terrain) / échelle)
```

L'attaque d'une équipe monte si elle marque plus que prévu ; la défense adverse
baisse d'autant (et symétriquement). Cela **distingue les équipes « spectacle »**
(forte attaque, défense passoire) des **équipes « solides »** (peu de buts marqués,
mais bétonnées) — deux profils qu'un Elo unique confond.

- Features : `home_off_elo`, `home_def_elo`, `away_off_elo`, `away_def_elo`, plus
  un résumé `off_def_diff` = `(off_dom − déf_ext) − (off_ext − déf_dom)`.
- **Sans fuite** : ratings lus **avant** le coup d'envoi, mis à jour après (testé).

### 3. Avantage du terrain SPÉCIFIQUE à la compétition

L'avantage du terrain n'est pas universel : très fort en **qualifs** (voyages,
altitude, ambiance), plus modéré dans certains championnats, et **nul en terrain
neutre**. On remplace la constante historique (`HOME_ADVANTAGE = 65`) par une table
par compétition (`HOME_ADVANTAGE_BY_COMP`, valeurs fixées — non apprises sur les
résultats, donc **aucune fuite**), utilisée :

- dans le calcul de l'Elo (simple **et** offensif/défensif) ;
- comme feature à part entière `home_advantage` (0 si neutre), pour que le modèle
  pondère lui-même son effet.

## Branchement propre (gating)

Comme pour les cotes de marché (P2), les colonnes P5 sont **toujours calculées et
stockées** ; elles ne deviennent des **entrées du modèle** que si
`P5_FEATURES_ENABLED` est vrai (`pipeline/xgb_model.active_feature_cols`). Mettre la
variable à `false` reproduit **exactement** le modèle d'avant P5.

> Différence clé avec P2 : les cotes de marché manquent pour un match à venir
> (Coupe du Monde…), donc P2 reste OFF. Les features P5, elles, sont **toutes
> calculables pour un match futur** → on peut les activer sans casser les
> prédictions à venir. **P5 est donc ON par défaut.**

## Le verdict — mesuré honnêtement (`pipeline/p5_eval.py`)

Walk-forward anti-fuite, 6 plis, mêmes barres que les phases précédentes. On
reconstruit les features **deux fois** (drapeau OFF puis ON) pour que la variante
« p5 » reflète **tout** le changement, y compris l'avantage du terrain par
compétition dans l'Elo lui-même.

| Domaine | RPS avant | RPS après | Δ RPS | Δ log-loss | ROI avant → après |
|---|---|---|---|---|---|
| **Clubs** (4 454 matchs testés) | 0,20051 | **0,19964** | −0,00087 | −0,00168 | −4,34 → **−3,43** |
| **Sélections** (24 703 matchs) | 0,18847 | **0,18798** | −0,00049 | −0,00149 | — (pas de cotes) |

- **Gain réel mais modeste** : le RPS et le log-loss baissent sur **les deux
  domaines**, sans aucune régression. Sur les sélections, le CSV de valeurs ne couvre
  pas les nations : le gain y vient **uniquement** de l'Elo off/déf + de l'avantage du
  terrain par compétition — un signal propre.
- **Le verdict des phases P1–P3 tient** : le modèle reste **bien calibré sans battre
  le marché**. Le ROI s'améliore (perd moins) mais **reste négatif** sur les clubs.
  P5 affine la probabilité, il ne crée pas d'avantage exploitable au pari.

## Où ça vit dans le code

| Élément | Fichier |
|---|---|
| Avantage terrain par compétition + Elo offensif/défensif | `pipeline/elo.py` (`HOME_ADVANTAGE_BY_COMP`, `home_advantage_for`, `compute_elo_offdef`) |
| Chargement + appariement de la valeur d'effectif | `pipeline/squad_value.py` |
| Données de valeur d'effectif (éditable) | `data/squad_values.csv` |
| Features (off/déf, valeur, avantage terrain) + état pour prédire | `pipeline/features.py` |
| Liste de features active (gating) | `pipeline/xgb_model.py` (`P5_FEATURE_COLS`, `active_feature_cols`) |
| Expérience avant/après (RPS/log-loss/ROI) | `pipeline/p5_eval.py` |
| Réglages | `config.py` : `P5_FEATURES_ENABLED`, `SQUAD_VALUE_CSV/_URL` |

## Tests (`tests/test_p5.py`, 11 cas + anti-fuite mutualisé)

Parsing & appariement de la valeur d'effectif (commentaires/vides ignorés, exact +
flou, inconnu rejeté, repli sans fichier) ; avantage du terrain (nul si neutre,
varie par compétition) ; Elo off/déf (sépare attaque/défense, **sans fuite**,
départ à 1500) ; gating des features P5. Le test anti-fuite existant
(`test_features_no_leak.py`) compare **toutes** les colonnes : il couvre donc
automatiquement les nouvelles features P5. **Suite complète : 127 tests verts.**

## Critère d'acceptation — atteint

✅ Valeur d'effectif comme feature pré-match, alignée par les noms
✅ Elo séparé en offensif / défensif
✅ Avantage du terrain spécifique à la compétition (nul en terrain neutre)
✅ Réentraînement + comparaison RPS / calibration / ROI avant-après
✅ Tests anti-fuite
✅ **Delta documenté honnêtement : gain modeste mais réel, sans régression**

## Rappel honnête

P5 rend le modèle **un peu plus précis** (RPS/calibration), de façon reproductible et
sans fuite. Il **ne le rend pas gagnant** face aux bookmakers : le ROI reste négatif.
C'est un raffinement de la qualité probabiliste, pas une martingale. Aucun conseil de
pari, aucune promesse.
