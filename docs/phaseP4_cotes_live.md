# Phase P4 — Comparateur de cotes multi-bookmakers en live (line shopping)

## L'idée, en une phrase

À proba égale, **mieux vaut encaisser 2,10 que 1,95**. On interroge en direct des
**dizaines de bookmakers** d'un coup, on affiche le **meilleur prix par issue**, et
on le croise avec la proba du modèle pour signaler la **value**.

## Ce que ça fait concrètement

Pour une affiche (ex. *Canada — Bosnie-Herzégovine*, Coupe du Monde 2026) :

| Issue | Meilleur prix | Bookmaker | Proba modèle | Avantage | Value |
|---|---|---|---|---|---|
| Victoire Canada | **1,92** | Pinnacle | 62 % | +17,9 % | ✅ |
| Match nul | 3,65 | Betfair | 26 % | −6,9 % | — |
| Victoire Bosnie | 5,10 | Smarkets | 12 % | −38 % | — |
| Plus de 2,5 buts | 2,35 | Betsson | 59 % | +39 % | ✅ |
| Moins de 2,5 buts | 1,67 | BetOnline | 41 % | −32 % | — |

- **Meilleur prix** = la cote la plus haute trouvée parmi tous les books couverts.
- **Avantage (edge)** = `proba_modèle × meilleur_prix − 1`. Vert si positif.
- **Value** = avantage au-dessus du seuil (`BET_EDGE_THRESHOLD`, +5 %).

## La source : the-odds-api.com

- Palier gratuit (500 requêtes/mois). **Clé dans `.env`** (`ODDS_API_KEY`),
  **jamais** renvoyée au frontend ni versionnée.
- Marchés récupérés : `h2h` (1X2) et `totals` (plus/moins 2,5 buts), en cote décimale,
  régions `eu,uk`.
- Compétitions couvertes (table `SPORT_KEYS`) : 5 grands championnats + Coupe du Monde,
  Euro, Ligue des Nations, qualifs CdM.
- **Quota visible** : chaque appel renvoie le nombre de requêtes restantes ; on
  l'affiche dans l'UI et via `/api/odds/status` (garde-fou anti-dépassement).

## Économie d'appels : le cache

Les cotes live passent par le **cache disque** (`http.fetch`), avec un TTL
configurable `ODDS_API_TTL_HOURS` (défaut **6 h**). Conséquence : consulter dix
affiches du même tournoi ne coûte **qu'un seul appel** (toutes les rencontres d'un
« sport » arrivent dans la même réponse), puis plus rien pendant 6 h. La clé API
n'entre pas dans la clé de cache (le fichier local ne dépend pas du secret).

## Repli propre, sans clé

C'est une **règle du projet** : tout doit marcher hors ligne / sans clé.

- Pas de clé → le module se déclare « non configuré ».
- On retombe alors sur les **cotes d'ouverture football-data** stockées en base
  pour le dernier match connu entre ces deux équipes, clairement étiquetées
  *« historique, pas un prix live »*.
- Aucune affiche trouvée chez les books non plus → message honnête, et le reste de
  l'app (prédiction, mise, simulation) fonctionne normalement.

## CLV en live (suivi de ligne, best-effort)

À chaque consultation, on enregistre les meilleurs prix captés dans la table
`odds_snapshots`. `/api/odds/clv` rapporte, par issue, le mouvement entre **notre
premier** et **notre dernier** prix observé (`first_odds / last_odds − 1`) :
positif = on avait capté une meilleure cote que la ligne actuelle.

> **Honnêteté** : la vraie CLV se mesure contre la cote de **clôture**. Sans
> planificateur tournant en permanence, on rapporte le mouvement des prix que l'on a
> effectivement captés au fil des consultations — un **proxy** utile, pas la CLV de
> clôture exacte. C'est documenté tel quel dans la réponse de l'API.

## Appariement des noms

Les bookmakers écrivent « Bosnia & Herzegovina », « USA »… On relie ces libellés à
nos noms canoniques par **appariement flou** (réutilise `names._similarity`), avec
quelques forçages (`TEAM_OVERRIDES`, ex. `usa → United States`). On teste les deux
ordres domicile/extérieur et on retient le meilleur si le score dépasse 0,6.

## Où ça vit dans le code

| Élément | Fichier |
|---|---|
| Récupération + cache + parsing + meilleur prix + appariement | `pipeline/odds_api.py` |
| Quota API (rappel d'en-têtes) | `pipeline/http.py` (`on_headers`) |
| Table de suivi des prix captés | `pipeline/db.py` (`odds_snapshots`) |
| Comparateur + value + repli + CLV | `service.live_odds`, `odds_status`, `odds_clv_summary` |
| Endpoints | `GET /api/odds/live`, `/api/odds/status`, `/api/odds/clv` |
| Panneau UI « Meilleures cotes du marché » | `webapp/` |
| Réglages | `config.py` : `ODDS_API_KEY/_BASE_URL/_REGIONS/_MARKETS/_TTL_HOURS` |

## Tests (`tests/test_odds.py`, 11 cas)

Parsing 100 % hors ligne sur un échantillon fidèle : meilleur prix par issue,
filtre totals 2,5, robustesse JSON, appariement flou (+ ordre inversé + overrides),
mapping des sports, `fetch_odds` vide sans clé, repli football-data du service
(skipif modèles), statut configuré (skipif clé). **Suite complète : 116 tests verts.**

## Critère d'acceptation — atteint

✅ Tableau « meilleures cotes + value » par match à venir
✅ Repli sans clé fonctionnel
✅ Cache limitant les appels (quota suivi)
✅ Tests verts

## Rappel honnête

Le line shopping **augmente le prix encaissé** quand on parie, ce qui est réel et
mesurable. Mais il ne change pas le verdict des phases P1/P2 : le modèle reste **bien
calibré sans battre le marché** sur le 1X2. Cet outil aide à **exécuter au mieux** un
pari de value — il ne fabrique pas la value. Aucun conseil de pari, aucune promesse.
