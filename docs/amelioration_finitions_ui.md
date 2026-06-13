# Finitions de l'interface (écussons, recherche, « Pourquoi »)

Cette étape améliore trois détails déjà présents, sans rien ajouter de neuf.

## 1. Écussons de clubs (au lieu des seules initiales)

- Chaque club affiche désormais son **vrai écusson** quand on le connaît, sinon
  la pastille d'initiales colorée habituelle. **Aucun trou** possible : l'image
  n'est qu'un « plus » posé par-dessus le repli.
- Les logos viennent d'une **table figée** (`pipeline/club_crests.py`, 130 clubs)
  générée **une seule fois hors ligne** par `scripts/build_club_crests.py` depuis
  TheSportsDB. Au moment d'afficher le site, **aucun appel réseau** n'est fait :
  on lit la table, c'est instantané et gratuit.
- Le script désambiguïse par **pays** et écarte les homonymes (équipes
  féminines, réserves, jeunes, amateurs) pour ne jamais montrer le mauvais logo.
- Si une image ne charge pas (lien cassé un jour), le navigateur **retombe tout
  seul** sur la pastille d'initiales (écouteur d'erreur global côté `app.js`).

Pour régénérer la table un jour :
`.venv/bin/python -m scripts.build_club_crests`
(ou avec des noms de clubs en argument pour n'en corriger que quelques-uns).

## 2. Recherche d'équipe en tapant (typeahead)

- Les menus déroulants des équipes sont devenus des **champs de recherche** : on
  tape les premières lettres et la liste se filtre toute seule
  (`<input list="…">` + `<datalist>`, natif et accessible, sans JavaScript lourd).
- On peut donner soit le **nom**, soit l'identifiant : le backend résout les deux.
  Si le texte ne correspond à aucune équipe de la liste, un message clair invite
  à **choisir dans les propositions** (pas de prédiction sur une équipe inconnue).

## 3. « Pourquoi » un peu plus riche

L'explication factuelle du pronostic gagne deux éclairages, toujours sans fuite
de données (uniquement des faits connus avant le match) :

- **Confrontations directes (h2h)** : bilan des derniers face-à-face réels
  (ex. « Sur les 8 dernières confrontations directes, Arsenal mène 5 à 2 »),
  lu directement dans l'historique des matchs.
- **Repos** : si une équipe a nettement plus récupéré que l'autre, on le signale
  (ex. « Alpha a eu plus de repos avant le match : 9 j contre 3 j »).

## Tests

`168 passed` — dont les nouveaux tests d'écussons (présence du logo + repli
initiales garanti, jamais d'écusson sur une sélection) et le test du « Pourquoi »
enrichi (facteurs h2h et repos présents quand pertinents, absents sinon).
