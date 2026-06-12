# Phase 2 — Les features (variables d'entrée du modèle)

## En une phrase

Pour chaque match, on calcule un jeu de variables décrivant les deux équipes
**telles qu'on les connaissait juste avant le coup d'envoi** — jamais avec une
information postérieure. C'est la matière première des modèles de la Phase 3.

## Comment les générer

```bash
.venv/bin/python -m pipeline.features
```

Cela écrit deux tables dans la base : `features_club` et `features_intl`.

## La règle d'or : zéro fuite de données

Le calcul parcourt les matchs dans l'ordre du temps et, pour chaque match, ne lit
que l'état des équipes construit à partir des matchs **strictement antérieurs**.
L'état n'est mis à jour qu'**après** avoir produit la ligne. Conséquence vérifiée
par les tests : si on ajoute des matchs futurs, **aucune ligne passée ne change**.

> Un vrai bug a été trouvé et corrigé grâce à ce test : l'Elo était auparavant
> aligné « par position » après un tri non stable, ce qui pouvait attribuer le
> rating d'un match à un autre match joué le même jour. Désormais l'alignement se
> fait par identifiant de match, et le tri Elo est stable.

## Liste des features

| Feature | Définition | Source | Impact attendu |
|---|---|---|---|
| `home_elo`, `away_elo`, `elo_diff` | Rating Elo de chaque équipe avant le match, et leur différence | moteur Elo maison | **Le plus prédictif.** Niveau global des équipes. |
| `home_form5_ppg`, `away_form5_ppg` | Points par match sur les 5 derniers matchs | résultats | Forme récente. |
| `home_form5_gf` / `_ga` | Buts marqués / encaissés moyens sur 5 matchs | résultats | Dynamique offensive / défensive courte. |
| `home_form10_*`, `away_form10_*` | Mêmes mesures sur 10 matchs | résultats | Forme de fond, moins bruitée. |
| `home_xg5` / `home_xga5` | xG marqués / concédés moyens sur 5 matchs (clubs) | understat | Qualité réelle du jeu, au-delà du score (signal fort). |
| `home_rest_days`, `away_rest_days` | Jours depuis le dernier match | résultats | Fatigue / fraîcheur. |
| `home_played`, `away_played` | Nombre de matchs déjà joués | résultats | Fiabilité de la forme (peu de matchs = forme peu informative). |
| `neutral` | Terrain neutre (1) ou non (0) | données | Annule l'avantage du terrain (crucial pour la CdM). |
| `comp_importance` | Importance de la compétition (0,3 amical → 1,0 CdM) | barème | Enjeu du match ; pondère le sérieux des équipes. |
| `h2h_home_winrate`, `h2h_home_gd` | Confrontations directes récentes (5 dernières) du point de vue du club qui reçoit | résultats | **Poids faible** : petit ajustement, données rares. |

Les colonnes `odds_home/draw/away` sont aussi recopiées : ce **ne sont pas des
entrées du modèle** (ce serait tricher), elles servent uniquement de benchmark en
Phase 3.

## Couverture (run du 12/06/2026)

- Clubs : **99,3 %** des matchs ont une forme et un xG glissants renseignés
  (les 0,7 % restants sont les tout premiers matchs de chaque équipe, sans passé).
- L'écart d'Elo va d'environ −510 à +560, ce qui correspond bien à l'écart entre
  une grosse équipe à domicile et un promu.
- Sélections : l'importance de compétition prend bien plusieurs valeurs (amical,
  qualif, Nations League, phases finales) ; 26 % des matchs sont sur terrain neutre.

## Choix assumés

- Pas de features fragiles (compositions exactes, météo) : risque de
  surapprentissage tant que les données sont limitées (règle d'or n°6). On reste
  sur des agrégats robustes.
- Les premiers matchs d'une équipe ont des features de forme à `NaN` (aucun passé).
  Les modèles de la Phase 3 gèrent ces valeurs manquantes explicitement.

## Tests (critère d'acceptation rempli)

`tests/test_features_no_leak.py` — 5 tests :
- ajout de matchs futurs sans effet sur les lignes passées (test anti-fuite clé) ;
- premier match d'une équipe sans historique de forme ;
- vérification chiffrée de la forme, du repos et du xG glissant ;
- contrôle anti-fuite sur un échantillon de la **vraie base**.
