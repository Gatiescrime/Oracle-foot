# Étape 8 — Redéploiement & récapitulatif

## L'idée, en une phrase

Vérifier que les 7 améliorations **cohabitent** sans rien casser, que la suite de
tests est **toute verte**, et rappeler **la commande à lancer pour publier** en ligne.

## Vérifications faites

- **Tests** : `156 passed` — suite complète verte (aucune régression).
- **Visuel** (aperçu local, les 4 onglets) :
  - en-tête corrigé — les 4 onglets passent sur **leur propre ligne centrée** quand la
    barre est étroite (avant : l'onglet actif débordait en ovale géant) ;
  - « Analyser un match », « Matchs à venir » (avec l'encart *Meilleure value du jour*),
    « Coupe du Monde 2026 » et « Track record » s'affichent correctement ;
  - drapeaux/écussons partout, date de mise à jour, ligne « pourquoi », chiffres réels.
- **PWA** : le service worker est **réseau d'abord** sur la coquille (CSS/JS) et **ne
  touche jamais aux `/api/`** → un redéploiement sert toujours la nouvelle version, pas
  de site figé. (Vérifié : après une mise à jour du CSS, un simple rechargement reprend
  la dernière version.)

## Récapitulatif des 8 étapes

| Étape | Apport | Où |
|---|---|---|
| 1 | Drapeaux & écussons partout où une équipe est nommée | `pipeline/badges.py` |
| 2 | Date de dernière mise à jour dans l'en-tête | `app_meta`, en-tête |
| 3 | Vue « Matchs à venir » (toutes compétitions, par jour) | `upcoming_matches`, `/api/upcoming` |
| 4 | Encart « Meilleure value du jour » (honnête, borné) | `best_value_today`, `/api/value/today` |
| 5 | Ligne « pourquoi » sous chaque prédiction (sans fuite) | `_explain`, `predict` |
| 6 | Page « Track record » (vrais chiffres du backtest) | `track_record`, `/api/track-record` |
| 7 | Application installable (PWA) | `manifest.json`, `sw.js`, `icon.svg` |
| 8 | Cohabitation vérifiée, tests verts, publication | ce document |

## Publier en ligne

Le site est hébergé sur **Render**, qui redéploie automatiquement à chaque `push` sur
`master`. Pour mettre en ligne ces améliorations :

```bash
git add -A
git commit -m "Améliorations site : drapeaux, matchs à venir, value, pourquoi, track record, PWA"
git push origin master
```

Render détecte le push, reconstruit l'image et redéploie tout seul (~2-3 min). Le
healthcheck `/api/competitions` valide que l'app répond avant de basculer.

> Rien n'est publié tant que tu n'as pas lancé `git push`. Dis-moi si tu veux que je
> prépare le commit pour toi.

## Critère d'acceptation — atteint

✅ Les 7 améliorations cohabitent, design inchangé (thème sombre, aurora, mode avancé)
✅ Suite complète verte : **156 tests**
✅ Commande de publication rappelée (commit + `git push origin master` → Render)
