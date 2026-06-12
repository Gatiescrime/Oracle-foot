# Étape 7 — Application installable (PWA)

## L'idée, en une phrase

Permettre d'**ajouter Oracle Foot à l'écran d'accueil** (mobile et desktop) et de le
lancer en plein écran comme une vraie app — sans jamais servir de données périmées.

## Comment ça marche

- **`manifest.json`** (servi à la racine `/manifest.json`) : nom « Oracle Foot »,
  affichage `standalone`, thème sombre (`#070b16`), icône. C'est ce qui rend l'app
  installable (« Ajouter à l'écran d'accueil »).
- **Icône** `webapp/icon.svg` : pastille dégradée vert→cyan→violet sur fond sombre,
  cohérente avec le `brand-mark` du site. SVG `sizes:"any"`, `purpose:"any maskable"`.
- **Meta tags** dans `<head>` : `theme-color`, `apple-touch-icon`, et les balises
  `apple-mobile-web-app-*` pour un lancement plein écran propre sur iOS.
- **Service worker** `webapp/sw.js` (servi à la racine `/sw.js` avec l'en-tête
  `Service-Worker-Allowed: /` pour contrôler tout le site) :
  - met en cache **uniquement la coquille statique** (`/`, `style.css`, `app.js`,
    icône, manifest) ;
  - joue **le réseau d'abord** (network-first) puis retombe sur le cache **hors-ligne** ;
  - **ne met JAMAIS en cache les appels `/api/`** → aucune donnée périmée, les mises à
    jour passent toujours.
  - le nom de cache est versionné (`oracle-foot-shell-v1`) et l'ancien cache est purgé
    à l'activation → pas d'app figée après un redéploiement.
- **Enregistrement** discret dans `app.js` (au `load`, échec silencieux si non supporté).

## Pourquoi pas de cache agressif

C'est la contrainte clé : un PWA mal réglé sert une vieille version. Ici, seule la
coquille est mise en cache (et en network-first), tandis que **toutes les données**
(prédictions, cotes, matchs à venir, track record) passent **toujours par le réseau**.
Le bouton « Mettre à jour » et la fraîcheur des données restent donc intacts.

## Tests

`tests/test_service_api.py::test_pwa_assets_served` :
- `/manifest.json` répond 200, `display=standalone`, icône SVG ;
- `/sw.js` répond 200 avec `Service-Worker-Allowed: /` ;
- le service worker contient bien la logique d'exclusion des `/api/`.

**Suite complète verte : 156 tests.**

## Critère d'acceptation — atteint

✅ Manifest + meta tags + service worker minimal → « Ajouter à l'écran d'accueil »
✅ Lancement plein écran, thème sombre, icône Oracle Foot
✅ Aucun cache agressif : les données ne sont jamais bloquées / périmées

## Où ça vit

| Élément | Fichier |
|---|---|
| Manifest | `webapp/manifest.json` (route `/manifest.json`) |
| Icône | `webapp/icon.svg` |
| Service worker | `webapp/sw.js` (route `/sw.js`, scope racine) |
| Meta tags + enregistrement | `webapp/index.html` (`<head>`), `webapp/app.js` (fin) |
| Routes racine | `pipeline/api.py` (`manifest`, `service_worker`) |
