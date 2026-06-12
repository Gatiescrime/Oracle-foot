# Phase P6 — Contexte : qualité de tir, calendrier, déplacement, météo

## L'idée, en une phrase

On teste **les dernières variables de contexte** dont on pouvait espérer un gain —
qualité fine des tirs, congestion/déplacement, météo pour les marchés over/under —
puis on **mesure honnêtement** leur apport… et **on jette tout ce qui n'aide pas**.

## Ce qu'on a construit (et qui reste calculé, même si non retenu)

Toutes ces colonnes sont **calculées et stockées** match par match, **sans fuite**
(lues avant le coup d'envoi, mises à jour après). Elles servent d'audit et permettront
une ré-évaluation future ; mais — verdict ci-dessous — **aucune n'entre dans le modèle**.

### A. Qualité de tir (proxys glissants, clubs uniquement)

Sur une fenêtre des 5 derniers matchs passés :
- `home_xg_per_shot`, `away_xg_per_shot` — xG par tir (qualité des occasions créées) ;
- `home_sot_ratio`, `away_sot_ratio` — part de tirs cadrés ;
- `home_finishing`, `away_finishing` — finition = buts − xG (sur/sous-performance) ;
- `home_def_xg_per_shot`, `away_def_xg_per_shot` — qualité des tirs **concédés**.

### B. Calendrier (congestion + déplacement)

- `home_matches_14d`, `away_matches_14d` — nombre de matchs joués dans les 14 jours
  **précédents** (fatigue / accumulation) ;
- `home_travel_km`, `away_travel_km` — distance (haversine) entre le **lieu du match
  précédent** de l'équipe et le stade du jour ; **NaN** si un lieu est inconnu ou en
  terrain neutre. Coordonnées des stades dans `data/venues.csv` (versionné, éditable),
  alignées sur nos noms canoniques (appariement exact puis flou, comme P5).

### C. Météo (visée over/under)

- `temp_c`, `precip_mm`, `wind_kmh` — température moyenne, cumul de pluie, rafales,
  récupérées par **Open-Meteo** (archive, sans clé), groupées par stade et **mises en
  cache sur disque**. Pour un match **à venir**, l'archive ne couvre pas l'avenir → la
  météo est **NaN** (gérée nativement par XGBoost), sans casser la prédiction.

## Branchement propre (gating)

Comme P2/P5, les colonnes P6 ne deviennent des **entrées du modèle** que si
`P6_FEATURES_ENABLED` est vrai et qu'elles figurent dans
`xgb_model.P6_FEATURE_COLS`. **Cette liste est désormais vide** (voir verdict) →
`active_feature_cols()` revient **exactement** au modèle livré en P5 (36 features).

## La mesure — propre et anti-fuite (`pipeline/p6_eval.py`)

Walk-forward, 6 plis, par-dessus le modèle livré (P5 ON). Comme les colonnes P6 sont
**purement additives** (elles ne modifient pas l'Elo), on reconstruit les features
**une seule fois** et chaque variante n'est qu'un **sous-ensemble de colonnes** donné à
XGBoost → comparaison exacte, sur les mêmes plis.

> **Détail méthodo important.** Pendant l'expérience, on force
> `colsample_bytree = 1.0`. Sinon, ajouter des colonnes (même vides) change le
> sous-ensemble de features échantillonné par chaque arbre → un **bruit** qui masque le
> vrai signal. Avec 1.0, une variante n'ajoutant que des colonnes inertes donne un delta
> **exactement nul** : on isole ainsi l'apport réel de chaque groupe. (Vérifié : la
> météo en NaN donnait bien `+C = 0,0` partout.)

On juge sur **deux barres** : RPS / log-loss du 1X2 **et** Brier / log-loss du
over/under 2,5 (là où la météo était censée peser), plus le ROI au pari.

### Résultats (deltas vs baseline P5, clubs)

Baseline : RPS 0,19991 · log-loss 1,00135 · O/U Brier 0,24313 · O/U log-loss 0,67927 · ROI −3,07 %.

| Variante | Δ RPS | Δ log-loss | Δ O/U Brier | Δ O/U log-loss | Δ ROI |
|---|---|---|---|---|---|
| **+A_tir** (qualité de tir) | −0,00019 | −0,00477 | −0,00008 | −0,00024 | **−0,97** |
| **+B_calendrier** (congestion+voyage) | −0,00022 | −0,00013 | +0,00003 | +0,00006 | −0,17 |
| **+C_meteo** (météo, données réelles) | **+0,00009** | **+0,00085** | −0,00022 | −0,00049 | −0,20 |
| **+tout** (A+B+C ensemble) | +0,00002 | **+0,01253** | −0,00016 | −0,00041 | **−1,60** |

*(Δ négatif = mieux pour RPS / log-loss / Brier ; Δ ROI négatif = pire au pari.)*

## Le verdict : **on jette tout** (discipline anti-surapprentissage)

- **Aucun gain honnête.** Les meilleurs deltas (`+A_tir` en log-loss, `+B` en RPS) sont
  **un ordre de grandeur sous P5** (qui apportait −0,00087 RPS / −0,00168 log-loss),
  donc **dans le bruit**.
- **La météo ne tient pas sa promesse.** Sur l'over/under, le gain est microscopique
  (Brier −0,00022, soit ~0,1 % relatif) **et** elle **dégrade le 1X2** (log-loss
  +0,00085). Hypothèse « météo → totals » : **non confirmée**.
- **Le ROI baisse à chaque ajout** (−0,17 à −0,97 point), jamais l'inverse.
- **Signature claire de surapprentissage** : combiner les groupes (`+tout`) **régresse
  nettement** (log-loss +0,0125, ROI −1,60). Des signaux réellement additifs ne se
  comporteraient pas ainsi — ils sont bruités/corrélés et le modèle les surapprend.

Conformément à la règle posée dès le départ — *« on jette toute variable qui n'aide
pas »* — `P6_FEATURE_COLS` est **vide**. Les colonnes restent calculées et stockées
(audit, ré-évaluation future), mais **n'entrent pas** dans XGBoost. Le modèle livré
reste **strictement** celui de P5.

## Où ça vit dans le code

| Élément | Fichier |
|---|---|
| Géo : haversine, lecture/appariement des stades, helpers congestion/voyage | `pipeline/context.py` |
| Météo : Open-Meteo (archive, batch par stade, cache, repli NaN) | `pipeline/weather.py` |
| Coordonnées des stades (éditable) | `data/venues.csv` |
| Features P6 (qualité de tir, congestion, voyage, météo) + état pour prédire | `pipeline/features.py` |
| Liste de features active / gating | `pipeline/xgb_model.py` (`P6_FEATURE_COLS` **vide**, `active_feature_cols`) |
| Expérience walk-forward A/B/C/tout (colsample=1.0) | `pipeline/p6_eval.py` |
| Réglages | `config.py` : `P6_FEATURES_ENABLED`, `VENUES_CSV`, `WEATHER_*` |

## Tests (`tests/test_p6.py`, 11 cas + anti-fuite mutualisé)

Géométrie (haversine, distance connue Madrid–Barcelone) ; lecture des stades
(commentaires/lignes invalides ignorés) ; appariement exact + flou + inconnu rejeté ;
congestion = matchs récents **passés uniquement** (anti-fuite) ; déplacement NaN si lieu
inconnu ; proxys de tir glissants sur le **seul passé** ; repli météo (désactivée → rien ;
mapping correct via stub réseau) ; **gating** des features P6. Le test anti-fuite existant
(`test_features_no_leak.py`) compare **toutes** les colonnes → il couvre automatiquement
les nouvelles features P6.

## Critère d'acceptation — atteint

✅ Métriques de tir fines là où disponibles (xG/tir, cadrage, finition, tirs concédés)
✅ Contexte calendrier : distance de déplacement, congestion (repos déjà présent)
✅ Météo (température, pluie, vent), visée over/under
✅ Chaque ajout passe le test anti-fuite et son impact est **chiffré** contre la baseline
✅ **On jette toute variable qui n'aide pas — variables retenues : aucune, documenté ci-dessus**

## Rappel honnête

P6 est une phase au **résultat négatif assumé** : on a construit, mesuré proprement,
et **conclu que ces variables n'apportent rien d'exploitable** — ni en précision, ni au
pari. C'est exactement la discipline anti-surapprentissage voulue. Le verdict des phases
P1–P5 tient : le modèle est **bien calibré sans battre les bookmakers**. Aucun conseil de
pari, aucune promesse.
