# Apprentissage — PHASE 2 : Track record vivant

La page **Track record** affiche désormais DEUX choses, clairement distinguées :

1. **Historique réel des prédictions** (nouveau) — la performance des prédictions
   **réellement faites par le site**, notées après coup à partir du journal (Phase 1).
2. **Backtest historique** (existant) — on rejoue l'histoire en walk-forward. Sert de
   référence, mais ce ne sont pas de « vraies » prédictions en conditions réelles.

## Ce qu'affiche l'historique réel

À partir des prédictions **réglées** du journal (`journal.live_track_record`) :

- **Taux de réussite 1X2** : part des matchs où l'issue la plus probable a gagné ;
- **RPS réel** et **score de Brier** moyens (qualité des probabilités) ;
- **Courbe de calibration** des prédictions réelles (annoncé X % → observé combien) ;
- **RPS dans le temps** (par mois), dès qu'il y a au moins deux périodes ;
- **Par compétition** : effectif, réussite, RPS de chaque compétition ;
- **CLV** et **ROI** **uniquement si** des cotes ont été captées (sinon masqués, en
  toute honnêteté — la plupart des sélections n'ont pas de cotes).
- Le nombre de prédictions **en attente** de résultat est aussi indiqué.

Tant qu'aucune prédiction n'a encore été jouée, le panneau l'affiche franchement
(« Pas encore de prédiction évaluée ») au lieu d'inventer des chiffres.

## Pourquoi distinguer les deux

Le backtest mesure le modèle sur le passé (reproductible, gros volume). L'historique
réel mesure ce que le site a **vraiment annoncé**, sans recalage a posteriori : c'est
la mesure la plus honnête de l'exactitude au fil de l'eau, mais elle démarre petite et
se remplit au fil des matchs (Coupe du Monde en tête). Les deux sont présentées côte
à côte, étiquetées sans ambiguïté.

## Ce qui a changé dans le code
- `pipeline/journal.py` : `live_track_record()` (agrégats : réussite, RPS, Brier,
  calibration, par compétition, RPS dans le temps, CLV/ROI si dispo).
- `pipeline/service.py` : `track_record()` renvoie désormais une clé `live` (journal)
  EN PLUS du backtest ; `available` reste vrai dès que l'un OU l'autre existe.
- `webapp/app.js` + `index.html` : panneau « Historique réel des prédictions » au-dessus
  du backtest, avec repli honnête si le journal est vide.

## Tests (verts)
`tests/test_journal.py::test_live_track_record_aggregates` : règle deux prédictions
(un favori gagne, un favori perd) et vérifie réussite 0,5, RPS/Brier présents,
calibration et agrégat par compétition. Vérifié aussi en navigateur (panneau réel
distinct du backtest, 67 % de réussite sur un jeu de démo, aucune erreur console).

**Suite complète : 214 tests verts.**

## Critère d'acceptation — atteint
✅ La page Track record montre la performance RÉELLE issue du journal (réussite, RPS,
   calibration, par compétition ; CLV/ROI si cotes présentes)
✅ Clairement distinguée du backtest historique
✅ Honnête : repli explicite si pas encore d'historique, aucun chiffre inventé
