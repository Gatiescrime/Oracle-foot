# Phase 6 — Simulation Monte-Carlo de la Coupe du Monde 2026

## En une phrase

On rejoue tout le tournoi des **milliers de fois** pour estimer, équipe par équipe, sa
probabilité de sortir des poules, d'atteindre les quarts, la finale, et de soulever le
trophée.

## Comment ça marche (`pipeline/simulate.py`)

1. **Inférence des groupes** : on déduit les 12 groupes de 4 directement du calendrier
   réel (`fixtures`). Les équipes qui se rencontrent en phase de poules forment un
   groupe (union-find).
2. **Pré-calcul des matrices** : pour chaque affrontement possible (~1 100 paires), on
   demande **une seule fois** au modèle sa matrice des scores (terrain neutre). Simuler
   un match revient ensuite à tirer un score dans cette matrice — instantané. On évite
   ainsi des millions d'appels au modèle.
3. **Phase de groupes** : round-robin, barème 3/1/0, départage par différence de buts
   puis buts marqués. Qualifiés = les 2 premiers de chaque groupe + les 8 meilleurs
   troisièmes (format officiel à 48 équipes → 32 qualifiés).
4. **Élimination directe** : bracket à têtes de série jusqu'au titre. Pas de match nul
   (tirage au sort 50/50 en cas d'égalité, en lieu et place des tirs au but).

On agrège les tours atteints sur l'ensemble des simulations → probabilités par équipe.

## Honnêteté : l'approximation du bracket

L'appariement **exact** des 8es de finale dépend d'une grille FIFA complexe liée aux
positions de groupe qualifiées. On utilise ici un **bracket à têtes de série** (le mieux
classé affronte le moins bien classé). Les probabilités de titre sont donc **indicatives
et cohérentes avec la force estimée**, mais ne reproduisent pas le tirage exact de la
FIFA. C'est documenté en tête du module.

## L'API

`GET /api/simulate?n_sims=2000` → `{n_sims, n_groups, n_teams, rounds, teams:[...]}`.
Chaque équipe : `p_advance`, `p_quarter`, `p_final`, `p_title`. Coûteux (matrices +
milliers de tournois) → le résultat est **mis en cache** côté serveur (`lru_cache`).
`n_sims` est borné à `[100, 5000]`.

## L'interface

Onglet **Coupe du Monde 2026** → bouton « Lancer la simulation » → tableau classé par
probabilité de titre, du plus favori au moins favori.

## Résultat type (2 000 simulations)

| Équipe | Sortie poules | Quart | Finale | Titre |
|---|---|---|---|---|
| Argentina | 99 % | 45 % | 33 % | 23 % |
| Spain | 100 % | 35 % | 23 % | 14 % |
| England | 98 % | 24 % | 13 % | 7 % |
| Brazil | 97 % | 25 % | 13 % | 7 % |
| France | 93 % | 19 % | 10 % | 6 % |

Hiérarchie cohérente avec la force estimée des sélections (Elo + modèle). Les valeurs
restent indicatives : voir la note d'honnêteté ci-dessus.

## Critère d'acceptation (tests `tests/test_simulate.py`)

- 12 groupes de 4 inférés depuis le calendrier.
- Probabilités bien formées (∈ [0, 1], décroissantes du tour le plus précoce au titre).
- Une équipe nettement plus forte ressort favorite en tête de classement.
- Aucun appel au vrai modèle dans les tests : on injecte un échantillonneur factice.
