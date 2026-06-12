# Étape 3 — Vue « Matchs à venir » (toutes compétitions)

## L'idée, en une phrase

Un nouvel onglet qui liste les **vraies affiches des prochains jours**, regroupées par
date, avec drapeaux/pastilles, et un bouton **« Analyser »** qui ouvre la prédiction
du match en un clic.

## Comment ça marche

- **Source live** : si une clé the-odds-api est configurée, on agrège les événements
  de toutes les compétitions couvertes (`odds_api.SPORT_KEYS` : 5 grands championnats +
  Coupe du Monde, Euro, Nations League, qualifs). Pour chaque événement dans la fenêtre
  `[maintenant, +N jours]`, on capte au passage les **meilleures cotes** disponibles.
- **Appariement des noms** : les libellés bookmaker sont résolus vers nos équipes via
  `_resolve_fuzzy` (exact → alias `TEAM_OVERRIDES` → appariement flou ≥ 0,6). Un match
  qu'on ne sait pas relier de façon fiable est **ignoré** plutôt que mal affiché.
- **Repli honnête** : sans clé (ou si aucun événement live), on retombe sur la table
  `fixtures` (mêmes affiches, sans cotes). La réponse indique toujours sa `source`.
- **Cache agressif** : le résultat est mis en cache au niveau process pendant
  `ODDS_API_TTL_HOURS` (6 h) pour préserver le quota mensuel de la clé. Le cache est
  vidé par `service.clear_caches()` (donc après chaque refresh).
- **Frontend** : nouvel onglet « Matchs à venir » (chargé à la première ouverture, pas
  au démarrage). Les matchs sont groupés par jour (date en français), chaque ligne a
  drapeau/pastille + heure + compétition + bouton « Analyser ». Le bouton réutilise la
  fonction partagée `analyzeMatch(...)` (factorisée depuis la vue Coupe du Monde).

## Réponse de `/api/upcoming?days=7`

```json
{
  "days": 7, "source": "the-odds-api", "configured": true, "count": 26,
  "matches": [
    { "date": "2026-06-13", "commence_time": "2026-06-13T18:00:00Z",
      "competition": "FIFA World Cup", "domain": "international",
      "home": "Brazil", "away": "Morocco",
      "home_team_id": "...", "away_team_id": "...",
      "home_badge": { "kind": "flag", "iso": "br", ... },
      "away_badge": { "kind": "flag", "iso": "ma", ... },
      "neutral": false, "has_odds": true }
  ]
}
```

## Tests

`tests/test_service_api.py` :
- `test_upcoming_with_odds_source` : avec clé (stubbée), l'agrégation renvoie bien
  l'affiche dans la fenêtre, avec cotes et écussons.
- `test_upcoming_fallback_to_fixtures` : sans clé, repli propre sur `fixtures`
  (`has_odds=False`), réponse cohérente même si la fenêtre est vide.

**Suite complète verte : 151 tests.**

## Critère d'acceptation — atteint

✅ On voit les affiches des prochains jours de plusieurs ligues (ici la Coupe du Monde
   est en cours ; les championnats apparaîtront dès leur reprise)
✅ Regroupées par jour, avec drapeaux/pastilles et meilleures cotes captées
✅ Un clic « Analyser » lance la prédiction du match
✅ Repli propre sans clé (table `fixtures`)

## Où ça vit

| Élément | Fichier |
|---|---|
| Agrégation + appariement flou + cache | `pipeline/service.py` (`upcoming_matches`, `_resolve_fuzzy`, `_fixtures_in_window`) |
| Endpoint | `pipeline/api.py` (`GET /api/upcoming`) |
| Onglet + vue + rendu | `webapp/index.html` (`#view-upcoming`), `webapp/app.js` (`loadUpcoming`, `analyzeMatch`) |
| Style | `webapp/style.css` (`.upcoming`, `.fx.up`, `.up-comp`) |
