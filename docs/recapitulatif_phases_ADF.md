# Récapitulatif — Plan d'amélioration (Phases A → F)

Vue d'ensemble des six phases, du résultat obtenu, et des **limites connues**
(honnêteté : on dit ce que le modèle fait ET ce qu'il ne fait pas).

## Ce qui a été fait

| Phase | Objet | Résultat | Doc |
|---|---|---|---|
| **A** | Mise à jour fiable + résultats CdM | Rapide ne re-télécharge que la saison en cours (quelques s) ; timeout par source + global ; **vrai bouton Annuler** ; un match de CdM terminé bascule vers l'historique (Elo/features) et quitte « à venir » (testé). | [maj_fiable.md](maj_fiable.md) |
| **B** | Sélections plus justes | Correction **inter-confédérations** (sans fuite) : l'Égypte (et autres dominateurs de viviers faibles) dégonflée, Belgique favori net. RPS intl **0,1949 → 0,1941**, log-loss amélioré, clubs inchangés. Affichage honnête « >99 % ». | [modele_intl.md](modele_intl.md) |
| **C** | Comparateur de cotes | Alias de noms étendus (plus d'affiches live, moins de repli). **Garde-fou anti fausse-value** : value « à confirmer » sur les outsiders où le modèle est en fort désaccord avec un marché dévigé (plus strict en sélections). | [cotes.md](cotes.md) |
| **D** | Navigation & interface | **Bouton précédent du navigateur** (API History) : onglets + résultats → formulaire, sans recharger. Écussons clubs (130/130, repli initiales). « Pourquoi » riche. Rappel discret sur le « score le plus probable ». | [navigation_ui.md](navigation_ui.md) |
| **E** | Vitesse | Cache **navigateur** : réanalyse d'un match déjà vu **instantanée, sans scintillement** (aucune roue, panneau jamais masqué). Pas de doublon d'historique. Cache serveur déjà en place. | [amelioration_vitesse.md](amelioration_vitesse.md) |
| **F** | Vérification + publication | Suite complète **205 tests verts**. Ce récapitulatif + rappel de publication. | ce fichier |

## Limites connues (assumées, documentées)

1. **Clubs : le modèle ne bat pas (encore) le bookmaker.** Au backtest chronologique,
   RPS clubs ≈ 0,215 contre ≈ 0,193 pour le bookmaker, et le « value betting » est
   légèrement négatif. Le site est calibré et honnête là-dessus (page Track record) ;
   il **ne promet aucun gain**.
2. **Sélections : pas de validation « contre le marché ».** Nos données historiques
   internationales **n'ont pas de cotes** : impossible de prouver qu'on bat le marché
   sur les sélections. On a amélioré ce qui est mesurable (RPS, calibration) et on
   **n'a pas forcé** le modèle à coller au marché (ex. Belgique) — ce serait
   invérifiable et nuirait à la calibration. Pour l'avis du marché, voir le comparateur.
3. **Couche « actualité » (LLM) en repli.** Le proxy ne relaie pas la recherche web :
   la couche qualitative reste le plus souvent **neutre** (aucun ajustement). Le socle
   statistique fonctionne sans elle ; aucune donnée inventée.
4. **Comparateur de cotes live = clé the-odds-api requise.** Sans clé (ou hors
   couverture), repli **propre** sur les cotes football-data **historiques** (signalé
   comme tel, pas un prix live).
5. **Effet pays hôte (Mondial) = bonus fixe** (USA/Canada/Mexique), choisi a priori et
   **jamais appris** (anti-fuite) : il pousse dans le bon sens sans prétendre à une
   valeur « optimale ».
6. **`data/football.db`** est modifié par les exécutions (tests / app) : c'est un
   **artefact de fonctionnement**, exclu des commits de ces phases.

## Publication (rappel)

Tout le travail des phases A→F est **déjà commité** (6 commits, de `Étape 2` à
`Phase E` + ce récap). Pour publier (Render redéploie automatiquement) :

```bash
rm -f .git/index.lock          # seulement si un verrou git traîne
git push origin master
```

> Le seul changement non commité restant est `data/football.db` (artefact). Si tu
> fais `git add -A && git commit`, tu l'embarqueras (sans danger, mais inutile) ;
> pour l'éviter, `git checkout -- data/football.db` **après avoir arrêté le serveur
> local** (ne pas écraser la base pendant qu'elle est ouverte).
