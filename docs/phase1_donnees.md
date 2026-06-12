# Phase 1 — Le socle de données

## En une phrase

Une seule base SQLite (`data/football.db`), propre et reconstructible par une commande,
qui rassemble les résultats de matchs (clubs + sélections), les cotes des bookmakers et
les xG, le tout avec des noms d'équipes unifiés entre sources.

## Comment rafraîchir les données

```bash
.venv/bin/python -m pipeline.refresh            # rapide (réutilise le cache disque)
.venv/bin/python -m pipeline.refresh --no-cache # force le re-téléchargement complet
```

La commande repart toujours d'une base neuve : on peut la relancer autant qu'on veut
sans jamais créer de doublon (chaque match a un identifiant calculé à partir de sa date
et des deux équipes).

## Ce que contient la base

| Table | Contenu |
|---|---|
| `teams` | Une ligne par équipe, avec un identifiant canonique stable (`team_id`), le nom affiché, le domaine (club / sélection) et le pays. |
| `team_aliases` | **La table de correspondance des noms.** Relie chaque libellé d'une source (football-data, understat, martj42) à l'identifiant canonique. |
| `matches` | Tous les matchs joués : date, équipes, score, compétition, saison, terrain neutre, tirs/tirs cadrés, **xG domicile/extérieur**, et **cotes bookmaker**. |
| `fixtures` | Les matchs à venir (dont le calendrier complet de la Coupe du Monde 2026). |
| `ingest_log` | Trace de chaque exécution (source, lignes, statut). |

## Chiffres actuels (run du 12/06/2026)

- **8 907 matchs de clubs** (5 grandes ligues × 5 saisons, 2021/22 → 2025/26).
- **49 405 matchs de sélections** (1872 → 2026).
- **70 matchs à venir**, qui sont l'intégralité du calendrier de la CdM 2026.
- **130 clubs** et **336 sélections** identifiés ; 596 alias de noms.
- **Couverture xG clubs : 100 %.** **Couverture cotes clubs : 90 %** (les saisons récentes
  sont mieux fournies en cotes que les plus anciennes).

## Les sources

| Source | Apporte | Accès |
|---|---|---|
| football-data.co.uk | Résultats, tirs, **cotes** des grandes ligues | CSV par ligue/saison |
| understat.com | **xG / xGA** par match (clubs) | endpoint JSON `getLeagueData` |
| martj42 (GitHub) | Résultats des sélections + calendrier CdM 2026 | un seul CSV |

Pour les cotes, on retient en priorité la **cote de clôture Pinnacle** (la référence du
marché), avec repli sur Bet365 puis la moyenne du marché. C'est ce qui servira de
benchmark en Phase 3 (« battre le bookmaker »).

## La correspondance des noms (le point délicat)

Chaque source écrit les équipes différemment : football-data dit « Man City », understat
dit « Manchester City ». Pour les clubs, football-data fait foi. Comme understat et
football-data ont le **même groupe d'équipes** dans une ligue donnée une saison donnée, on
les apparie par un **appariement optimal** (on relie les deux listes de façon à maximiser la
ressemblance globale, pas équipe par équipe). Cela évite les confusions classiques comme
« Atletico Madrid » / « Athletic Bilbao ». Quelques cas connus sont forcés à la main.
Les sélections n'ont qu'une source (martj42), leurs noms sont donc directement canoniques.

## Robustesse

- Téléchargements avec ré-essais ; en cas de coupure réseau, repli automatique sur le cache
  local (la base reste reconstructible hors ligne une fois les fichiers téléchargés une fois).
- Tout est journalisé dans `data/refresh.log` et dans la table `ingest_log`.

## Tests (critère d'acceptation rempli)

`.venv/bin/python -m pytest` — 15 tests verts :
- hors-ligne : correspondance des noms (y compris les paires piégeuses) et logique de
  dédoublonnage ;
- sur la base réelle : nombre de matchs au-dessus des seuils, **aucun doublon**, intégrité
  référentielle (toute équipe référencée existe), couverture xG > 50 % et cotes > 80 %,
  présence du calendrier CdM 2026.

## Limites connues / choix assumés

- **clubelo non intégré** : on calcule l'Elo nous-mêmes (plus robuste et reproductible) —
  la table d'alias est prête à l'accueillir si besoin plus tard.
- La couverture en cotes décroît sur les saisons anciennes (limite de la source).
- Les xG ne couvrent que les clubs (understat ne fournit pas les sélections).
