# PHASE C — Comparateur de cotes : couverture + garde-fou anti fausse-value

Deux objectifs : faire **remonter les vraies cotes live plus souvent** (moins de
repli football-data), et **ne plus présenter de « fausses value » criardes** comme
des opportunités sûres.

## 1. Meilleure correspondance des noms (the-odds-api ↔ notre base)

Le comparateur n'affiche les cotes live que s'il **retrouve l'affiche** chez
the-odds-api. L'appariement (`odds_api.find_event`) se fait déjà en deux temps et
dans les deux sens (domicile/extérieur inversés) :
1. **clé d'alias** (`names.alias_key`) : accents et casse neutralisés (slugify) +
   table d'alias de **mots réellement différents** ;
2. sinon **appariement flou** sur les libellés.

Ce qui a été renforcé : la table `names.NATION_ALIASES` couvre désormais davantage de
variantes fréquentes des bookmakers / the-odds-api, surtout pour les nations de la
Coupe du Monde 2026 :

```
USA → United States        UAE → United Arab Emirates
Czechia → Czech Republic   Macedonia / FYR Macedonia → North Macedonia
Korea Republic → South Korea   Türkiye → Turkey   China PR → China
Côte d'Ivoire → Ivory Coast    Trinidad & Tobago → Trinidad and Tobago
Bosnia → Bosnia and Herzegovina   Hong Kong, China → Hong Kong   …
```

> Les écarts d'**accent / de casse** (Curaçao, Türkiye…) étaient déjà gérés par
> `slugify` : on n'ajoute donc QUE les écarts de **mots**. Garde-fou vérifié par
> test : « Northern Ireland » ne tombe **jamais** sur la République d'Irlande.

Effet : plus d'affiches trouvées chez les bookmakers → moins de repli sur les cotes
football-data (historiques).

## 2. Garde-fou anti « fausse value »

### Le problème
L'« avantage » (edge) se calcule `proba_modèle × meilleure_cote − 1`. Sur un
**outsider**, une petite erreur de proba du modèle fabrique un edge **gigantesque
mais illusoire** (« +73 % de value »). C'est précisément là que le modèle est le
moins fiable — surtout en **sélections**, où sa calibration face au marché n'est pas
prouvée (pas de cotes historiques internationales pour la valider, cf.
[modele_intl.md](modele_intl.md)).

### Le correctif
- On compare le modèle à la **vraie** probabilité du marché : les cotes sont
  **dévigées** (marge du bookmaker retirée, `devig.fair_probs`) avant comparaison —
  `1/cote` brut surévalue la croyance du marché.
- Une value est marquée **« à confirmer / fiabilité faible »** (au lieu d'« opportunité »)
  quand **toutes** ces conditions tiennent :
  - le marché voit l'issue en **outsider** (proba équitable < seuil) ;
  - le modèle est en **fort désaccord** (proba modèle ≥ ratio × proba équitable).
- Seuils **plus stricts en sélections** qu'en clubs :

| | Outsider si proba marché < | Désaccord si modèle ≥ |
|---|---|---|
| Clubs | 0,35 | 1,6 × marché |
| Sélections | 0,50 | 1,35 × marché |

- On ne **masque pas** ces value (honnêteté) : on les **balise**. Mais la rubrique
  « Meilleure value du jour » ne **met en avant** que les value fiables (les autres
  sont comptées à part, `n_flagged`).

### Où ça agit
- **Comparateur de cotes** (`/api/odds/live`) : chaque ligne porte `reliability`
  (`ok`/`low`), `value_reliable` et `market_fair_prob`. L'UI affiche un badge orange
  **« value à confirmer »** + une note explicative.
- **Reco de mise** (`/api/stake`) : si la value tient sur un outsider en fort
  désaccord, un **avertissement** est ajouté en tête et le badge devient
  **« Value à confirmer »**.
- **Meilleure value du jour** (`/api/value/today`) : les value peu fiables sont
  **écartées** du palmarès et comptées dans `n_flagged`.

Exemple réel (vérifié) : Égypte (extérieur) à la cote 6,5 face à la Belgique en
Coupe du Monde → edge brut +44 %, mais marché ~15 % vs modèle 22 % →
**« value à confirmer »**, pas une opportunité sûre. Un favori de club à edge
modéré reste, lui, une value **fiable**.

## Ce qui a changé dans le code
- `pipeline/names.py` : `NATION_ALIASES` étendue (variantes the-odds-api / bookmakers).
- `pipeline/config.py` : seuils du garde-fou (`VALUE_OUTSIDER_FAIR_MAX_*`,
  `VALUE_DISAGREE_RATIO_*`, `VALUE_TRUST_MIN_BOOKS`).
- `pipeline/service.py` : `_fair_market_probs` (devig), `_value_reliability`,
  champs `reliability` / `value_reliable` / `market_fair_prob` dans `_build_rows` ;
  `best_value_today` écarte les value peu fiables (`n_flagged`) ; `recommend_stake`
  ajoute l'avertissement.
- `webapp/app.js` + `webapp/style.css` : badge orange « value à confirmer » + note.

## Tests (verts)
- `tests/test_names.py` : 14 variantes the-odds-api → nom canonique (même clé) ;
  Irlande du Nord non confondue ; `find_event` apparie avec alias + ordre inversé.
- `tests/test_value_guard.py` : fiabilité (outsider + désaccord → « low »), seuils
  clubs vs sélections, devig (somme = 1, ordre préservé), `_build_rows` balise une
  fausse value et laisse une value saine fiable.

**Suite complète : 205 tests verts.**

## Critère d'acceptation — atteint
✅ Plus d'affiches appariées (alias étendus) → moins de replis football-data quand la clé est active
✅ Plus de « fausses value » criardes sur les outsiders (surtout internationaux) : balisées « à confirmer », écartées du palmarès
✅ Comparaison honnête au marché (cotes dévigées), garde-fou plus strict en sélections
