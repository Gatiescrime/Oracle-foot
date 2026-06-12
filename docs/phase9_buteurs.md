# Étape 3 / Phase 9 — Buteurs probables (anytime scorer)

## En une phrase

Pour un match de **club**, l'app affiche, par équipe, la **probabilité que chaque
joueur marque au moins une fois** — calculée à partir des buts attendus de l'équipe,
répartis entre les joueurs selon leur production récente et leurs minutes.

## D'où viennent les données joueur

Le même endpoint understat déjà utilisé pour le xG (`getLeagueData/{ligue}/{saison}`)
renvoie aussi une liste `players` : par joueur et par saison, on récupère
**buts, xG, minutes, matchs joués, poste**. Aucune page HTML à gratter, aucune
nouvelle source réseau. Les noms sont décodés (entités HTML) et l'équipe understat
est reliée au `team_id` canonique via les **alias understat** déjà posés au rattachement
du xG.

- Nouvelle table `players (understat_id, team_id, player_name, season, games, minutes,
  goals, npg, xg, npxg, assists, position)`, reconstruite à chaque refresh.
- Ingestion réelle vérifiée : **13 601 lignes joueurs sur 130 équipes** (5 grands
  championnats × 5 saisons).

## Le modèle (sans fuite, sans fausse précision)

Pour chaque équipe :

1. Le modèle de match (Dixon-Coles + XGBoost + calibration) fournit ses **buts
   attendus** `λ` — exactement le même chiffre que la prédiction 1/X/2 (couche actu
   incluse si elle est activée).
2. Chaque joueur reçoit un **poids** :
   `poids = taux_pour_90 × (minutes_attendues / 90) × fiabilité`
   - `taux_pour_90` = mélange 50 % buts / 50 % xG ramené à 90 min (réduit le bruit) ;
   - `minutes_attendues` = minutes/match de la saison, plafonnées à 90 (un remplaçant
     pèse moins qu'un titulaire) ;
   - `fiabilité` ∝ minutes jouées (plein crédit à partir de ~450 min) : empêche un
     joueur « 1 match / 1 but » d'exploser le classement.
3. Buts attendus du joueur : `g_i = λ × poids_i / Σ poids` (la somme distribuée vaut λ).
4. Probabilité **anytime** (marque ≥ 1) : `P = 1 − exp(−g_i)` (Poisson).

## Lien avec la couche actualité (Étape 1)

Le calcul accepte une liste de joueurs **indisponibles** (`unavailable_home` /
`unavailable_away`) : un joueur écarté passe à **0 minute** → poids nul → retiré de la
liste, et son poids est redistribué aux autres. Vérifié : exclure Haaland fait
remonter Foden en tête de Man City.

## Sélections : dégradation propre

Les données joueur sur les sélections sont trop pauvres pour une estimation honnête.
L'endpoint renvoie alors **`available: false`** avec un message clair
(« Données joueur indisponibles pour les sélections… ») — **jamais** une liste inventée.

## Interface

Section « Buteurs probables » sous la prédiction (onglet *Match libre*) : deux colonnes
(domicile / extérieur), top 6 par équipe avec barre de probabilité. En sélection ou si
une équipe n'a pas de données, un message le dit sans afficher de faux chiffres.

## Endpoint

`POST /api/scorers` →
`{available, domain, exp_home_goals, exp_away_goals, home:{team, data, scorers:[{name,
position, prob, exp_goals, per90, minutes, goals}]}, away:{…}}`.

## Vérification

- **Chaîne réelle** : Man City–Liverpool → Haaland **52 %**, Foden 17 %, Reijnders 15 %…
  côté City ; Ekitike 20 %, Salah 15 %, Gakpo 12 %… côté Liverpool. Les bons attaquants
  ressortent en tête, probabilités cohérentes avec le λ de l'équipe.
- **Sélection** (France–Brésil) → `available: false`, aucun chiffre inventé.
- **Tests (`tests/test_scorers.py`)** : classement du buteur prolifique en tête,
  exclusion d'un absent, liste vide si aucune donnée exploitable, somme des buts
  distribués = λ, joueur petit échantillon dégradé, dégradation sélection, endpoint API.
  **Suite complète : 69 verts.**
