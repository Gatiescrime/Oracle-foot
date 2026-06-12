# Étape 4 — Encart « Meilleure value du jour »

## L'idée, en une phrase

En haut de la vue **Matchs à venir**, mettre en avant les **2-3 meilleures
opportunités de value** du moment : l'issue, la meilleure cote, le bookmaker et
l'écart (edge) entre la probabilité du modèle et le marché — sans jamais promettre
un gain.

## Comment ça marche

- On balaye les affiches à venir (`upcoming_matches`) qui ont des cotes live.
- Pour chacune, on **réutilise exactement** `live_odds` (proba du modèle × meilleure
  cote captée → edge, drapeau `value` au-dessus du seuil `BET_EDGE_THRESHOLD`). Aucune
  logique de value dupliquée : c'est le même moteur que le comparateur de cotes.
- On agrège toutes les issues « value », on les classe par edge décroissant, on garde
  le **top N** (3 par défaut).
- **Cache** : résultat mis en cache au niveau process (même TTL que les cotes, 6 h)
  pour préserver le quota d'appels.

### Garde-fou d'honnêteté (important)

Le backtest a montré que le modèle **ne bat pas le marché** et que le value betting
ressort négatif (−7,6 %). C'est sur les **outsiders extrêmes** que le modèle est le
moins fiable : un écart de proba minime y fabrique un edge gigantesque mais illusoire
(ex. « Curaçao bat l'Allemagne, +270 % »). Pour ne pas vendre du rêve à un public non
averti, on **filtre** les issues remontées :

- probabilité du modèle **≥ 12 %** (on écarte les paris à quasi-zéro chance) ;
- cote **≤ 8,0** (on écarte les cotes de pur loto).

Et un message rappelle clairement : *la value n'est pas une garantie de gain*.

## Réponse de `/api/value/today?days=3&top_n=3`

```json
{
  "configured": true, "edge_threshold": 0.05, "scanned": 11, "n_value": 21,
  "items": [
    { "competition": "FIFA World Cup", "home": "Brazil", "away": "Morocco",
      "selection": "away", "label": "Victoire Morocco",
      "best_odds": 6.0, "book": "Betsson", "model_prob": 0.36, "edge": 1.17,
      "home_badge": {...}, "away_badge": {...},
      "home_team_id": "...", "away_team_id": "...", "neutral": false }
  ]
}
```

## Frontend

- L'encart `#valueOfDay` s'affiche en tête de la vue « Matchs à venir » (chargé en
  même temps que la liste). Chaque ligne est **cliquable** : elle ouvre directement
  l'analyse du match (réutilise `analyzeMatch`).
- S'il n'y a **aucune** value (ou pas de clé API), l'encart reste masqué : pas de bruit.
- Mise en avant visuelle discrète (liseré vert), edge en vert, note de prudence en bas.

## Tests

`tests/test_service_api.py` :
- `test_best_value_today_surfaces_positive_edge` : avec des cotes hautes mais crédibles
  (≤ plafond), l'issue la plus probable dégage une value remontée en tête.
- `test_value_today_empty_without_key` : sans clé, l'encart renvoie une liste vide
  proprement (aucune erreur).

**Suite complète verte : 153 tests.**

## Critère d'acceptation — atteint

✅ En tête de « Matchs à venir », le top 3 des meilleures value (issue, cote, book, edge)
✅ Réutilise la logique existante (`live_odds` / edge vs seuil), zéro duplication
✅ Mention claire que la value n'est pas une garantie de gain
✅ Rien d'affiché si aucune value (ou pas de clé)
✅ Garde-fou contre les fausses value sur outsiders extrêmes (cohérent avec le backtest)

## Où ça vit

| Élément | Fichier |
|---|---|
| Scan + filtre + classement | `pipeline/service.py` (`best_value_today`) |
| Endpoint | `pipeline/api.py` (`GET /api/value/today`) |
| Encart | `webapp/index.html` (`#valueOfDay`), `webapp/app.js` (`loadValueOfDay`) |
| Style | `webapp/style.css` (`.value-of-day`, `.vod-row`, …) |
