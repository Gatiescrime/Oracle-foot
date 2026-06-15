# Apprentissage — PHASE 1 : journal des prédictions

But : se donner une **mémoire** des prédictions pour mesurer ensuite la performance
RÉELLE (Phase 2) et apprendre une correction bornée (Phase 3). Règle d'or absolue :
**zéro fuite** — une prédiction est enregistrée AVANT que le résultat soit connu.

## La table `predictions_log` (persistante)

Nouvelle table dans le **schéma persistant** (`_AUX_SCHEMA`) : elle **survit aux
refresh** (contrairement à `matches`/`fixtures` reconstruits à chaque mise à jour).
Elle stocke, par prédiction :

- horodatage, **version du modèle** (date d'entraînement du domaine), compétition,
  équipes, **options** (terrain neutre, couche actu) ;
- **probabilités prédites** 1/X/2, **buts attendus**, **score le plus probable** ;
- **cotes de marché captées** si disponibles (sinon vides) ;
- **statut** : `pending` (en attente) → `settled` (réglée) ;
- au règlement : **résultat réel**, issue, bon/mauvais 1X2, **Brier**, **RPS**, **CLV**.

Une contrainte d'unicité (version, équipes, date, options) **déduplique** : recliquer
sur le même match ne crée pas de doublon.

## Quand journalise-t-on ? (anti-fuite)

`journal.maybe_log` n'enregistre une prédiction que si **toutes** ces conditions
tiennent :
1. le match est au **calendrier** (`fixtures`) → il est donc **à venir**, avec une
   date connue (rattachement « équipes + fenêtre de date ») ;
2. le résultat n'est **pas déjà connu** (absent de `matches`) — sinon on refuse :
   impossible de « prédire » après coup ;
3. à l'enregistrement, **aucune** colonne de résultat n'est remplie (statut `pending`).

C'est **best-effort** : toute erreur de journalisation est avalée et ne casse jamais
la prédiction servie. Le journal couvre les matchs présents au calendrier (les
sélections, **Coupe du Monde incluse**) ; les analyses hypothétiques (hors calendrier)
ne sont pas journalisées.

## Quand règle-t-on ?

À **chaque mise à jour des données**, après reconstruction des matchs joués,
`journal.settle_pending` associe chaque prédiction `pending` au vrai match joué
(mêmes équipes, date proche) et calcule les métriques :
- **bon/mauvais 1X2** (l'issue la plus probable a-t-elle gagné ?) ;
- **score de Brier** = Σ (p_i − 1{issue})² ;
- **RPS** (ranked probability score) ;
- **CLV** = cote captée / cote de clôture − 1 (si les deux existent ; souvent les
  sélections n'ont pas de cotes → CLV vide).

Les résultats ne proviennent QUE des matchs réellement joués : aucune information
future n'entre au moment de la prédiction.

## Ce qui a changé dans le code
- `pipeline/db.py` : table `predictions_log` (persistante).
- `pipeline/journal.py` (nouveau) : `model_version`, `maybe_log`, `settle_pending`.
- `pipeline/service.py` : journalise dans `predict()` (chemin de calcul, jamais bloquant).
- `pipeline/refresh_job.py` : règle les prédictions en attente après chaque refresh.
- `pipeline/config.py` : `PREDICTION_LOG_ENABLED` (activable/désactivable).

## Tests (verts)
`tests/test_journal.py` : journalise uniquement les vrais matchs à venir ; **anti-fuite**
(refus si le résultat est déjà connu ; `pending` sans aucune colonne de résultat) ;
déduplication ; **règlement** qui remplit résultat + Brier + RPS (valeurs vérifiées) ;
drapeau de désactivation. Un `conftest.py` désactive le journal pour le reste de la
suite (pas d'écriture dans la vraie base).

**Suite complète : 213 tests verts.** Vérifié aussi de bout en bout sur une vraie
affiche (Belgique–Égypte) : ligne `pending` créée avec les probas, **sans résultat**.

## Critère d'acceptation — atteint
✅ Table `predictions_log` persistante (survit aux refresh)
✅ Chaque prédiction d'un vrai match à venir est journalisée, AVANT le résultat
✅ Après un refresh, les prédictions passées reçoivent leur résultat réel + leurs notes
✅ Test anti-fuite : jamais de résultat connu au moment de journaliser
