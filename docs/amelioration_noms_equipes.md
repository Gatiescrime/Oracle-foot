# Consolidation — Étape 3 : mieux apparier les noms d'équipes (cotes live)

## Le problème

Pour afficher les **meilleures cotes live** d'un match, on doit retrouver, dans la
réponse de the-odds-api, l'événement qui correspond à l'affiche demandée. Or les
bookmakers nomment souvent les **sélections** autrement que notre base :

| Notre base (canonique) | the-odds-api / FIFA |
|---|---|
| United States | USA |
| South Korea | Korea Republic |
| Czech Republic | Czechia |
| Ivory Coast | Côte d'Ivoire |
| Turkey | Türkiye |
| China | China PR |
| DR Congo | Congo DR |
| Republic of Ireland | Ireland |
| Cape Verde | Cabo Verde |

L'ancien appariement comparait nos noms aux libellés de l'API par **similarité de
texte** : « United States » vs « USA » score ~0 → **aucun match trouvé**, repli sur
les cotes football-data. Un mécanisme de forçage existait (`TEAM_OVERRIDES`) mais
n'était appliqué **que de notre côté** : il ne corrigeait quasiment rien.

## La correction

### 1. Une table d'alias **symétrique**, centralisée
`pipeline/names.py` reçoit `NATION_ALIASES` : chaque **variante connue** (normalisée :
accents, casse et ponctuation retirés, espaces compactés) pointe vers le **nom
canonique** de notre base. Deux fonctions s'en servent :
- `resolve_alias(label)` : renvoie le nom canonique connu (sinon le libellé tel quel) ;
  couvre les **sélections** (`NATION_ALIASES`) **et les clubs** (réutilise la table
  `OVERRIDES` existante, ex. « Manchester City » → « Man City »).
- `alias_key(label)` : une **clé de comparaison stable**. « USA » et « United States »
  — ou « Man City » et « Manchester City » — produisent la **même** clé.

### 2. Appariement en deux temps, dans les deux ordres
`odds_api.find_event` (et la résolution d'un libellé bookmaker → équipe,
`service._resolve_fuzzy`) procèdent maintenant ainsi :
1. **Clé d'alias identique** des deux côtés → correspondance **exacte**, score 1.0
   (règle d'un coup tous les pièges du tableau ci-dessus) ;
2. sinon **appariement flou** sur les libellés déjà *résolus* (alias appliqués),
   pour les cas non listés.
Les deux ordres domicile/extérieur sont testés (l'API peut les intervertir).

### 3. Normalisation accents / casse / ponctuation
Déjà assurée par `_norm` (NFKD + minuscules + ponctuation → espace) ; on **compacte
en plus les espaces** pour que « Bosnia & Herzegovina » et « Bosnia and Herzegovina »
tombent sur la même clé.

## Effet attendu
Beaucoup moins de « Aucun match à venir trouvé chez les bookmakers… repli sur les
cotes football-data » : les affiches de **Coupe du Monde** et de grandes sélections
sont désormais appariées même quand le bookmaker écrit USA, Czechia, Côte d'Ivoire,
Korea Republic, etc. Priorité donnée aux nations, là où l'écart de libellé est le
plus fréquent.

## Tests
`tests/test_odds.py` (verts) :
- `test_nation_alias_resolution` : USA→United States, Korea Republic→South Korea,
  Czechia→Czech Republic, Côte d'Ivoire→Ivory Coast ; clés d'alias symétriques
  (USA≡United States, Man City≡Manchester City) ;
- `test_find_event_matches_via_nation_alias` : un événement API « USA – Korea
  Republic » est apparié à « United States – South Korea » (score 1.0) ; ordre
  inversé « Czechia » ↔ « Czech Republic » géré ;
- les tests existants (parsing, meilleur prix, ordre inversé, repli sans clé)
  restent verts.

**Suite complète : 163 tests verts.**

## Critère d'acceptation — atteint
✅ Les écarts de libellé fréquents (sélections, Coupe du Monde) sont résolus dans
   les deux sens d'appariement
✅ Table d'alias centralisée et symétrique (`names.NATION_ALIASES`), réutilisée par
   le comparateur de cotes **et** la résolution des libellés bookmaker
✅ Moins de replis « aucun match trouvé » ; aucun test existant cassé
