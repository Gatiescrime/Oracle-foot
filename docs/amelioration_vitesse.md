# Vitesse : cache des prédictions + chargement plus fluide

Cette étape rend le site plus rapide et plus agréable, sans changer les résultats.

## 1. Cache des prédictions identiques

- Quand on redemande **exactement le même match** (même compétition, mêmes deux
  équipes, mêmes options), le résultat est **réutilisé instantanément** au lieu
  d'être recalculé. C'est sans risque : le socle statistique est **déterministe**
  (mêmes données → même réponse).
- On ne met en cache **que le socle statistique** (couche actu désactivée). La
  couche « actualité » dépend du temps et du web : elle garde son propre cache à
  durée courte, on ne la fige donc pas ici.
- Chaque réponse servie est une **copie isolée** : modifier l'affichage côté
  navigateur ne peut jamais corrompre l'entrée en cache.
- **Invalidation automatique** : après un « rafraîchir les données » (nouveaux
  résultats, ré-entraînement), le cache est **vidé** (`service.clear_caches()`,
  déjà appelé par le job de refresh). On ne sert donc jamais une vieille
  prédiction après une mise à jour.
- Garde-fou mémoire : le cache se purge tout seul au-delà de 256 entrées.

## 2. États de chargement plus fluides

- Le bouton « Analyser » se désactive et affiche déjà une **roue d'attente**
  pendant le calcul principal.
- Nouveau : la carte « Actualité » (la plus lente, car elle interroge le web)
  affiche désormais un **message d'attente avec roue** — « Comparaison avec et
  sans l'actualité… » — au lieu d'apparaître tard et d'un coup. L'apparition du
  panneau est ainsi annoncée et progressive.

## Tests

`169 passed` — dont un nouveau test du cache : deux prédictions identiques
renvoient le même contenu mais des **objets distincts** (copie), une mutation de
la réponse ne corrompt pas l'entrée, et `clear_caches()` **vide bien** le cache.
