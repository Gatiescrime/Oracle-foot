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

## 3. Réanalyse instantanée, sans scintillement (Phase E)

Depuis l'ajout du bouton « précédent » (navigation par l'historique), un même match
peut être ré-affiché souvent : retour/suivant du navigateur, ou re-soumission. Pour
que ce soit **instantané et sans clignotement** :

- un **cache navigateur** mémorise le socle statistique déjà calculé (le modèle est
  déterministe). Quand on ré-affiche un match **déjà vu dans la session**, le résultat
  s'affiche **immédiatement**, **sans masquer le panneau ni montrer la roue** — donc
  aucun scintillement. Sinon, comportement normal (roue + calcul).
- ce cache navigateur est **vidé à chaque rechargement de page** ; or un refresh des
  données recharge la page → on ne ré-affiche jamais une vieille prédiction.
- la navigation **ne crée pas de doublon** d'historique pour un état identique
  (re-soumission du même match, clic sur l'onglet déjà actif) : le bouton précédent
  reste intuitif (un résultat → retour au formulaire, en une fois).

Vérifié en navigateur : à la réanalyse d'un match déjà calculé, le panneau de
résultat **n'est jamais masqué** et **aucune roue** n'apparaît (rendu synchrone) ;
aucune erreur console.

## Tests

Suite complète **verte**. Le cache **serveur** est couvert par
`test_prediction_cache_hit_and_invalidation` : deux prédictions identiques
renvoient le même contenu mais des **objets distincts** (copie), une mutation de la
réponse ne corrompt pas l'entrée, et `clear_caches()` **vide bien** le cache. Le
cache **navigateur** (anti-scintillement) a été vérifié en preview.
