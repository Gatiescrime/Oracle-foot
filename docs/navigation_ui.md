# PHASE D — Navigation et interface

Quatre points, sans toucher au parti pris graphique (sombre, fond animé, minimaliste
+ mode avancé).

## 1. Bouton « précédent » du navigateur (et geste retour mobile)

Avant, le bouton retour du navigateur quittait l'application (rien n'était empilé
dans l'historique). Désormais on utilise l'**API History** :

- **chaque changement d'onglet** (Analyser / Matchs à venir / Coupe du Monde / Track
  record) crée une entrée d'historique (`history.pushState`) ;
- **chaque analyse** d'un match en crée une aussi (avec la compétition et les deux
  équipes) ;
- le **bouton précédent** (ou le geste retour sur mobile) restaure l'état **sans
  recharger la page** :
  - depuis un résultat → retour au **formulaire** (résultats masqués) ;
  - depuis un onglet → retour à l'**onglet précédent** ;
  - le bouton **suivant** ré-affiche l'état quitté (le résultat est recalculé, donc
    toujours cohérent).

Mise en œuvre (`webapp/app.js`) : une bascule de vue **pure** (`applyView`), une
navigation qui empile (`navTo` → `pushState`), un applicateur d'état (`applyState`)
et un écouteur `popstate`. Un drapeau `NAV_RESTORING` garantit qu'une restauration
ne ré-empile jamais d'entrée. L'état initial est posé par `history.replaceState`.

## 2. Écussons des clubs

Les **130 clubs** des 5 ligues ont un **écusson** (logo) servi depuis une source
libre et stable (thesportsdb), avec **repli propre sur la pastille d'initiales** si
une image ne charge pas (écouteur d'erreur global, zéro trou). Couverture vérifiée :
130/130. Les images sont en `loading="lazy"` (chargées à l'affichage). Les sélections
gardent leur **drapeau**.

## 3. « Pourquoi » plus informatif

L'explication factuelle (`pipeline/service.py:_explain`) couvre déjà, en langage
simple et **sans aucune fuite** (uniquement des features pré-match) :
1. la **force Elo** (moteur principal) ; 2. la **forme récente** (5 derniers matchs) ;
3. l'**avantage du terrain** (ou son absence sur neutre) ; 4. l'**effet pays hôte**
du Mondial ; 5. les **confrontations directes** (bilan réel des face-à-face) ;
6. l'écart de **repos / fraîcheur** quand il est notable.

→ Les deux facteurs demandés (confrontations directes, repos) étaient donc déjà
présents ; on les conserve sans surcharger (au plus 6 facteurs, affichés seulement
s'ils sont pertinents).

## 4. Rappel sur le « score le plus probable »

Source de confusion (vue sur Belgique-Égypte) : le « score le plus probable » est le
score **individuel le plus fréquent** (souvent un petit score, 1-0 / 1-1), ce qui
**n'est pas** le « score attendu » ni le résultat le plus probable — un favori peut
gagner par beaucoup d'autres scores. On l'explique désormais discrètement :
- une **note** sous la valeur : « le plus fréquent, pas le score "attendu" » ;
- une **infobulle** (ℹ) qui renvoie vers les probabilités 1/N/2 et les buts attendus.

## Vérifié (preview navigateur)
- Navigation onglets + analyse : retour/suivant restaurent le bon état (onglet,
  formulaire, ou résultat) **sans rechargement** ; aucune erreur console.
- Écussons de clubs chargés (repli initiales prêt) ; note + infobulle du score
  présentes ; design global inchangé.
- Suite **pytest : 205 tests verts** (aucun code Python modifié dans cette phase).

## Critère d'acceptation — atteint
✅ Le bouton retour fonctionne intuitivement (onglets ET résultats → formulaire), sans recharger
✅ Écussons des clubs affichés, repli propre sur initiales
✅ « Pourquoi » informatif (Elo, forme, terrain, hôte, confrontations directes, repos)
✅ Rappel discret sur le « score le plus probable »
✅ Parti pris graphique inchangé
