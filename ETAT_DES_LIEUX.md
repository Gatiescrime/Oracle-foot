# Prédiction des résultats de matchs de foot — État des lieux & feuille de route

> Document de cadrage. Objectif: décider quoi construire et dans quel ordre, avant
> de lancer le développement avec Claude Code (modèle Fable 5).

Date: 12 juin 2026.

---

## 1. Verdict en une phrase

Le projet est **faisable et déjà amorcé**: les sources de données réelles sont
accessibles, et un prototype de modèle (Dixon-Coles / Poisson) a été entraîné et
testé de bout en bout sur de vraies données. Il reste à industrialiser, calibrer
et habiller d'une interface.

---

## 2. Ce qui est DÉJÀ prouvé (pas théorique, testé aujourd'hui)

### Sources de données réelles validées

| Source | Contenu | Accès | Volume récupéré |
|---|---|---|---|
| football-data.co.uk | Résultats + tirs + cartons + cotes bookmakers, grandes ligues | CSV direct, gratuit | **8 907 matchs** (PL, Liga, Bundesliga, Serie A, Ligue 1 × 5 saisons) |
| martj42/international_results | Résultats sélections 1872→2026 | CSV GitHub, gratuit | **49 405 matchs** joués + **70 matchs à venir dont la CdM 2026** |
| clubelo.com (API) | Elo des clubs, historique quotidien | API CSV, gratuit | testé OK |
| understat.com | xG / xGA par match et par équipe | scraping HTML/JSON | testé OK (à intégrer) |

Point fort inattendu: le dataset international **contient déjà le calendrier de la
Coupe du Monde 2026** (matchs futurs, scores vides). On peut donc prédire le
tournoi réel dès maintenant.

### Modèle déjà entraîné sur ces données

Un prototype Dixon-Coles tourne. Chiffres ci-dessous **vérifiés en ré-exécutant
le pipeline le 12 juin 2026** (et non recopiés d'un run précédent):

- Elo sélections (calculé maison, équipes actives) top 2026: **Espagne 2208,
  Argentine 2161, France 2114, Angleterre 2085, Brésil 2052, Colombie 2040,
  Portugal 2038** — classement réaliste.
- Modèle CLUBS (Premier League, 1900 matchs, **convergé**): Arsenal–Chelsea →
  victoire Arsenal 62%, nul 23%, Chelsea 15%, buts attendus 1.93–0.85, score le
  plus probable 2-0. Man City–Liverpool → 55/23/22, score probable 1-1. Cohérent.
- Modèle SÉLECTIONS (11 629 matchs, 262 équipes, **NON convergé** — voir §5):
  France–Brésil neutre → 59/21/19, buts 2.21–1.21, score 2-1. Résultat
  plausible mais **à ne pas prendre pour argent comptant tant que la convergence
  et la calibration ne sont pas réglées en Phase 3.**

Enseignement clé: le volet clubs est déjà fiable, le volet sélections demande
encore du travail d'optimisation (régularisation, restriction aux équipes
actives, plus d'itérations).

### Fichiers déjà produits dans ce dossier

```
data/        matches_clubs.parquet, matches_intl.parquet, fixtures_intl.parquet
pipeline/    ingest.py        -> téléchargement + nettoyage des données
             elo.py           -> moteur Elo (méthode World Football Elo)
             dixon_coles.py   -> modèle Poisson bivarié + correction Dixon-Coles
```

Ces fichiers sont un socle réutilisable, pas un livrable fini.

---

## 3. Architecture cible recommandée

Découplage en trois couches, pour que chaque morceau soit testable seul:

```
[ Données ]      ingestion -> stockage local (parquet/SQLite)
     |
[ Modèle ]       features (Elo, forme, xG...) -> Dixon-Coles + variante ML (XGBoost)
     |           export des paramètres entraînés -> model.json
     |
[ Interface ]    app web qui charge model.json et calcule les prédictions
```

**Choix de format d'interface conseillé**: une app web autonome (un seul fichier
HTML/JS qui embarque les paramètres du modèle et fait le calcul Poisson dans le
navigateur). Avantages: aucun serveur à faire tourner, l'utilisateur ouvre le
fichier et choisit deux équipes. Le ré-entraînement reste un script Python lancé
quand on veut rafraîchir les données.

Alternative si on veut du temps réel / mises à jour automatiques: app
Streamlit ou Flask (nécessite un serveur).

---

## 4. Feuille de route par étapes

Chaque phase est un livrable autonome qu'on peut confier à Claude Code.

### Phase 0 — Cadrage (ce document) ✅ fait
Sources validées, prototype prouvé, architecture choisie.

### Phase 1 — Pipeline de données solide
- Fiabiliser `ingest.py` (reprises sur erreur, cache local, log).
- Ajouter l'intégration **xG understat** pour les clubs (feature à fort impact).
- Harmoniser les noms d'équipes entre sources (table de correspondance).
- Stocker en SQLite plutôt qu'en parquet épars.
- **Livrable**: une base de données propre, rafraîchissable en une commande.

### Phase 2 — Feature engineering complet
Implémenter les features prioritaires du brief (sans fuite de données, tout
calculé "avant match"):
- différence Elo (déjà fait), forme 5 matchs, moyennes mobiles buts/xG marqués
  et encaissés, repos entre matchs, avantage terrain/neutre, phase de compétition.
- **Livrable**: un DataFrame de features documenté + tests anti-fuite.

### Phase 3 — Modèle + calibration sérieuse
- Finaliser Dixon-Coles (corriger les 2 points ci-dessous) **et** une variante
  XGBoost / Poisson régularisé, puis comparer.
- **Backtest** chronologique + **Ranked Probability Score** + courbe de
  calibration + comparaison aux cotes bookmakers (le vrai benchmark).
- **Livrable**: `model.json` + rapport de performance chiffré.

### Phase 4 — Interface utilisateur
- App web: choisir compétition + 2 équipes (+ terrain neutre), afficher
  proba 1/X/2, score le plus probable, matrice de scores, over/under, BTTS.
- Mode "Coupe du Monde 2026": prédire les vrais matchs à venir du calendrier.
- **Livrable**: l'app utilisable.

### Phase 5 — Itérations
- Simulation de tournoi (Monte-Carlo sur le bracket CdM).
- Suivi de performance live au fur et à mesure des résultats réels.
- Éventuel ré-entraînement automatique programmé.

---

## 5. Points de vigilance déjà identifiés

1. **Calibration de l'avantage terrain (Dixon-Coles)**: dans le prototype, le
   paramètre est ressorti ~0 car la moyenne de buts à domicile l'absorbe déjà.
   À séparer proprement pour bien gérer les matchs sur terrain neutre (CdM).
2. **Convergence sur 312 sélections**: l'optimisation n'a pas convergé sur le
   gros dataset international (trop de paramètres pour L-BFGS en 200 itérations).
   Solutions: régularisation, plus d'itérations, ou restreindre aux équipes
   actives + pondération temporelle plus forte.
3. **Peu de données par sélection**: ~8-10 matchs de CdM par équipe tous les 4
   ans. Compenser avec qualifs + Ligue des Nations + amicaux, pondérés par enjeu
   (déjà prévu dans le moteur Elo).
4. **Risque de surapprentissage**: éviter les features trop spécifiques (météo
   exacte, compositions exactes) tant que le volume de données est faible.
   Privilégier des agrégats robustes (Elo, moyennes mobiles).
5. **Pas de fuite de données**: toute feature doit être calculée uniquement à
   partir d'informations connues AVANT le coup d'envoi.
6. **Benchmark honnête**: la vraie mesure de succès n'est pas "ça a l'air juste"
   mais "fait-on mieux que les cotes des bookmakers" sur un backtest.

---

## 6. Décisions à prendre avant de coder

- Périmètre prioritaire de la Phase 1: clubs, sélections, ou les deux en
  parallèle ? (Les clubs ont plus de données → calibration plus facile pour
  démarrer; les sélections sont l'objectif CdM.)
- Format d'interface définitif: fichier HTML autonome vs app Streamlit/Flask.
- Profondeur d'historique clubs: 5 saisons (actuel) suffisent-elles ou on remonte
  plus loin ?

---

## 7. Comment confier ça à Claude Code

Chaque phase ci-dessus peut devenir un prompt autonome. Les modules existants
(`ingest.py`, `elo.py`, `dixon_coles.py`) servent de point de départ: il suffit
de pointer Claude Code dessus et de lui donner l'objectif de la phase + les
points de vigilance correspondants.
