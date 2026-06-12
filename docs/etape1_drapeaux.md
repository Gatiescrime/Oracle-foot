# Étape 1 — Drapeaux et écussons

## L'idée, en une phrase

Donner à **chaque équipe nommée** dans le site un repère visuel — **drapeau** pour une
sélection, **pastille d'initiales colorée** pour un club — **sans jamais laisser de trou**.

## Comment ça marche

Toute la logique de mapping vit dans **un fichier dédié et testé** : `pipeline/badges.py`.
Le frontend ne fait qu'**afficher** l'objet `badge` renvoyé par l'API — aucune liste de
pays codée en dur dans le JavaScript.

### `pipeline/badges.py`

- **Sélections → code pays ISO 3166-1 alpha-2** (servi par [flagcdn.com](https://flagcdn.com),
  CDN gratuit, sans clé). Les nations britanniques utilisent les sous-codes flagcdn
  `gb-eng` / `gb-sct` / `gb-wls` / `gb-nir`.
- **Lookup tolérant** : insensible à la casse, aux accents et à la ponctuation (réutilise
  `names._norm`), plus une table d'**alias** fréquents (`USA`, `Côte d'Ivoire`,
  `Korea Republic`…).
- **Emoji drapeau** (`flag_emoji`) dérivé du code ISO, car les `<option>` natives d'un
  menu déroulant **ne savent pas afficher d'image** — on y met donc l'emoji.
- **Repli systématique** : tout nom non reconnu (clubs, sélections non-FIFA, entités
  historiques…) retombe sur une **pastille** = initiales propres (`initials`, mots de
  liaison ignorés) + **couleur déterministe** stable (`color`, dérivée d'un hachage du nom,
  lisible sur fond sombre). Aucun appel réseau, aucun crash.

`badge(name, domain)` renvoie :
- sélection reconnue → `{"kind":"flag", "iso", "emoji", "label"}` ;
- tout le reste → `{"kind":"initials", "text", "color", "label"}`.

### Branchement dans l'API (champs **additifs**, rien de cassé)

| Endpoint | Ajout |
|---|---|
| `GET /api/teams` | chaque équipe reçoit `badge` |
| `POST /api/predict` | `home_badge`, `away_badge` |
| `GET /api/fixtures` | `home_badge`, `away_badge` par match |
| `GET /api/simulate` | `badge` par équipe (sans muter le résultat mis en cache) |

### Affichage (sans toucher au parti pris design)

`webapp/app.js` expose un helper `badgeHTML(badge)` :
- **drapeau** → `<img class="team-badge flag" src="https://flagcdn.com/…">` (lazy, `alt=""`
  décoratif car le nom est juste à côté) ;
- **pastille** → `<span class="team-badge pill">` colorée.

Branché dans : **sélecteurs** d'équipes (emoji drapeau pour les sélections), **barres 1X2**
de la carte résultat, liste **Matchs à venir**, et **simulation** Coupe du Monde. CSS
discret (`.team-badge.flag` / `.team-badge.pill`), classe distincte de l'ancienne `.badge`
(verdict de mise) pour éviter toute collision.

## Accessibilité

Drapeaux et pastilles sont **purement décoratifs** (`alt=""` / `aria-hidden`) : le nom de
l'équipe reste l'information lisible par les lecteurs d'écran. Pas d'animation ajoutée →
`prefers-reduced-motion` reste respecté.

## Tests (`tests/test_badges.py`, 10 cas, hors ligne)

Mapping exact + normalisé (accents) + alias + sous-codes UK ; emojis (alpha-2 et
sous-codes) ; initiales (mots de liaison, mot unique, vide) ; couleur déterministe et hex
valide ; objet `badge` (drapeau pour une sélection, pastille en repli, club toujours en
pastille). **Suite complète verte.**

## Critère d'acceptation — atteint

✅ Drapeaux corrects pour les grandes sélections (flagcdn, sous-codes UK inclus)
✅ Pastilles d'initiales colorées pour les clubs
✅ Mapping centralisé dans un fichier dédié, tolérant aux noms manquants (repli neutre)
✅ **Aucun trou visuel** : toute équipe non mappée obtient une pastille

## Où ça vit

| Élément | Fichier |
|---|---|
| Mapping + helpers (ISO, emoji, initiales, couleur, `badge`) | `pipeline/badges.py` |
| Enrichissement des réponses API | `pipeline/service.py`, `pipeline/api.py` |
| Helper d'affichage + branchements | `webapp/app.js` (`badgeHTML`, `optLabel`) |
| Styles | `webapp/style.css` (`.team-badge.flag`, `.team-badge.pill`) |
| Tests | `tests/test_badges.py` |
