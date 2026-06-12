# Phase P2 — Les cotes du marché comme variable d'entrée

## En une phrase

On teste, mesures à l'appui, si donner au modèle l'**avis du marché** (les cotes
d'ouverture, transformées en probabilités) améliore ses prédictions — et si ce
gain se traduit en **edge réel** au pari (la vérité de la Phase P1).

## Anti-fuite (rappel)

On n'utilise **que les cotes d'OUVERTURE** (pré-match), jamais la clôture. Elles
sont **dévigées** (`pipeline/devig.py`, méthode *power*) en probabilités qui
somment à 1, puis exploitées de deux façons, toutes deux **désactivées par
défaut** (le modèle livré reste « pur ») :

- **features** : on ajoute `p_mkt_home/draw/away` aux entrées de XGBoost
  (`MARKET_FEATURES_ENABLED`) ;
- **blend** : on mélange la sortie `p = (1−w)·modèle + w·marché`
  (`MARKET_BLEND_WEIGHT`).

## Méthode

Walk-forward chronologique (`pipeline/market_eval.py`), 3 605 matchs de clubs avec
cotes, mesuré sur **deux** barres simultanément :
- **qualité** : RPS et log-loss hors échantillon ;
- **exploitabilité** : nombre de value bets, rendement et CLV (moteur de la
  Phase P1).

## Résultats (clubs, seuil d'edge 5 %)

| Variante | RPS | log-loss | Paris | Rendement | CLV moyen | Bat clôture |
|---|---|---|---|---|---|---|
| **baseline** (modèle pur) | 0,19885 | 1,01581 | 4 899 | −8,3 % | −0,18 % | 48,6 % |
| **features** (marché en entrée XGB) | **0,19569** | **0,98767** | 4 175 | −5,7 % | −0,02 % | 48,8 % |
| blend w=0,3 | 0,19589 | 0,97340 | 4 111 | −6,8 % | −0,12 % | 48,6 % |
| blend w=0,5 | 0,19448 | 0,96849 | 3 403 | −6,9 % | −0,10 % | 48,2 % |
| blend w=0,7 | 0,19353 | 0,96534 | 2 289 | −7,0 % | +0,26 % | 49,7 % |
| blend w=0,9 (≈ marché) | 0,19303 | 0,96379 | 1 525 | −6,1 % | +0,65 % | 52,5 % |
| **marché seul** (référence) | 0,19295 | 0,96360 | 1 516 | −6,3 % | +0,59 % | 52,3 % |

> Poids de blend optimal au sens du RPS : **w = 0,9** (RPS 0,19303), c'est-à-dire
> quasiment « copier le marché ».

## Lecture honnête

1. **Oui, le marché améliore la qualité probabiliste.** Plus on s'en rapproche,
   plus le RPS et le log-loss baissent (RPS 0,19885 → 0,19295 ; log-loss 1,016 →
   0,964). Le critère de la phase — « amélioration mesurable de la calibration et
   du RPS » — est **rempli**.

2. **L'approche `features` est le bon compromis.** Donner les probabilités de
   marché en *entrée* de XGBoost capte une grande partie du gain de log-loss
   (1,016 → 0,988) **sans réduire le modèle à un perroquet** du marché. Le modèle
   garde sa propre identité tout en s'aidant du signal de marché.

3. **Mais ça ne crée AUCUN edge au pari.** Le rendement reste **négatif partout**
   (−5,7 % à −8,3 %), y compris pour « marché seul » (−6,3 % = on paie la marge en
   pariant les probas du marché à ses propres prix). Se rapprocher du marché, c'est
   **être d'accord avec lui** : les value bets se raréfient (4 899 → 1 525) et on
   continue de perdre la marge. Le CLV légèrement positif des blends lourds est le
   même artefact qu'en Phase P1 (sélections très alignées sur Pinnacle), sans
   rentabilité.

## Décision (verrouillée)

- Les deux leviers restent **désactivés par défaut**. Le modèle livré demeure
  « pur » et **fonctionne pour n'importe quel match** — y compris les rencontres à
  venir (Coupe du Monde, sélections) pour lesquelles **aucune cote n'est
  disponible** en base. Activer les features marché obligerait à disposer des
  cotes d'ouverture au moment de la prédiction, ce qui n'est pas le cas en live.
- La capacité est **prête et mesurée** : pour qui opère uniquement sur des matchs
  dont la cote d'ouverture est connue, `MARKET_FEATURES_ENABLED=true` (après
  ré-entraînement) améliore RPS et calibration. À noter : **cela n'apporte pas de
  rentabilité** — cohérent avec le verdict P1.

## Conclusion

> Le marché rend nos probabilités **plus justes** mais pas **plus exploitables**.
> C'est un résultat scientifique net : améliorer le RPS en copiant le marché
> n'invente pas d'edge. Le vrai levier d'un avantage reste l'information que le
> marché n'a pas encore intégrée à l'ouverture — précisément la cible de la couche
> actualité (Phase 7), pas des cotes elles-mêmes.

## Vérification

- `python -m pipeline.market_eval` : expérience complète (baseline, features,
  blend balayé, marché seul) avec RPS, log-loss, paris et CLV.
- `tests/test_market.py` : **8 tests** — features de marché (somme = 1, NaN si
  cote absente), blend (w=0 → modèle, w=1 → marché, intermédiaire normalisé, poids
  borné), bascule de la liste de features. **Suite complète : 95 verts.**
