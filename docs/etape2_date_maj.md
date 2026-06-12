# Étape 2 — Date de dernière mise à jour dans l'en-tête

## L'idée, en une phrase

Afficher discrètement, en haut de page, **« Données à jour au JJ/MM/AAAA »**, pour que
l'utilisateur sache d'un coup d'œil la fraîcheur des résultats.

## Comment ça marche

- L'horodatage existe déjà : `refresh_job` persiste `last_updated` (table `app_meta`) à
  chaque mise à jour réussie. On l'expose via un **endpoint léger** `GET /api/meta`
  (`service.app_meta`).
- **Repli honnête** : si la machine n'a jamais lancé de mise à jour (`last_updated` absent),
  on renvoie aussi `latest_match_date` = date du dernier match en base, qui reflète tout
  autant la fraîcheur des données livrées. Le frontend prend `last_updated` en priorité,
  sinon `latest_match_date`.
- **Frontend** : au chargement, `loadMeta()` appelle `/api/meta`, formate la date en
  français (`JJ/MM/AAAA`) et l'affiche dans un `<span class="last-updated">` discret de
  l'en-tête (masqué sur très petits écrans pour ne pas surcharger). En cas d'erreur,
  l'en-tête reste simplement vide — aucune régression.

## Réponse de `/api/meta`

```json
{ "last_updated": "2026-06-12T18:40:52+00:00", "latest_match_date": "2026-06-11" }
```

## Tests

`tests/test_refresh_job.py::test_app_meta_exposes_last_updated` : après un refresh
enregistré, `/api/meta` renvoie bien `last_updated` (et le repli `latest_match_date`).
**Suite complète verte.**

## Critère d'acceptation — atteint

✅ La date s'affiche dans l'en-tête, discrète, format `JJ/MM/AAAA`
✅ Elle correspond au dernier refresh (repli sur la date du dernier match sinon)

## Où ça vit

| Élément | Fichier |
|---|---|
| Métadonnées (last_updated + repli) | `pipeline/service.py` (`app_meta`) |
| Endpoint léger | `pipeline/api.py` (`GET /api/meta`) |
| Affichage en-tête | `webapp/index.html` (`#lastUpdated`), `webapp/app.js` (`loadMeta`) |
| Style | `webapp/style.css` (`.last-updated`) |
