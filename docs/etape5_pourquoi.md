# Étape 5 — Ligne « pourquoi » sous chaque prédiction

## L'idée, en une phrase

Sous la recommandation, expliquer **en langage simple pourquoi** le modèle penche
d'un côté : force globale (Elo), forme récente, avantage du terrain.

## Comment ça marche

- `/api/predict` renvoie désormais un champ `why = { summary, factors }`.
- L'explication est calculée par `_explain(...)` à partir des **seules features déjà
  utilisées par le modèle** (`elo_diff`, `home_form5_ppg` / `away_form5_ppg`,
  `home_advantage`, `neutral`). **Aucune nouvelle donnée**, **aucune fuite** : on ne
  fait que traduire ce que le modèle voit déjà au moment de la prédiction.
- Trois facteurs, formulés en clair :
  1. **Force globale (Elo)** : qui est devant et de combien (écart en points, qualifié
     « légèrement / sensiblement / nettement » ; « au coude-à-coude » si < 20 pts).
  2. **Forme récente** : points par match sur les 5 derniers (comparés entre les deux).
  3. **Avantage du terrain** : mentionné à domicile, signalé absent sur terrain neutre.
- Une **synthèse** d'une phrase résume vers qui le modèle penche, et pourquoi d'abord.

## Exemple

```
Pourquoi
Le modèle penche pour Man City, d'abord sur la force globale (Elo).
 • Au classement Elo, Man City est sensiblement devant (écart de 107 pts).
 • Meilleure forme récente pour Man City (1.6 contre 1.0 pt/match sur 5 matchs).
 • Man City profite de l'avantage de jouer à domicile.
```

Sur terrain neutre (Coupe du Monde), le dernier point devient : *« Terrain neutre :
pas d'avantage du domicile. »*

## Frontend

Bloc `#recoWhy` ajouté dans la carte de recommandation (pleine largeur, sous la barre
de confiance) : une étiquette « Pourquoi », la synthèse, puis la liste des facteurs.
Masqué proprement si l'explication est vide.

## Tests

`tests/test_service_api.py::test_predict_includes_factual_why` : `why` présent, synthèse
non vide, au moins 2 facteurs, mention « domicile » hors terrain neutre et « neutre »
en Coupe du Monde.

**Suite complète verte : 154 tests.**

## Critère d'acceptation — atteint

✅ Une explication courte et factuelle s'affiche sous la recommandation
✅ Basée uniquement sur des features déjà utilisées (Elo, forme, avantage terrain) — sans fuite
✅ Langage simple, compréhensible sans connaissances foot

## Où ça vit

| Élément | Fichier |
|---|---|
| Calcul de l'explication | `pipeline/service.py` (`_explain`, champ `why` dans `predict`) |
| Affichage | `webapp/index.html` (`#recoWhy`), `webapp/app.js` (`renderPrediction`) |
| Style | `webapp/style.css` (`.reco-why`, `.rw-*`) |
