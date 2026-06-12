# Phase P1 — Backtest de PARIS (la métrique de vérité)

## En une phrase

On ne se contente plus de mesurer si nos probabilités sont *justes* (RPS) : on
mesure si elles sont **exploitables face au marché**, en simulant des paris dans
l'ordre du temps, sans jamais regarder l'avenir.

## Pourquoi cette phase

Le RPS dit « tes probabilités sont bien calibrées ». Le pari dit « gagnes-tu de
l'argent contre le bookmaker ? ». C'est la seule question qui tranche. Un modèle
peut être bien calibré **et** incapable de battre le marché — c'est précisément
ce que confirme ce backtest.

## Anti-fuite (règle d'or, non négociable)

football-data fournit deux familles de cotes. On les **sépare strictement** :

| Famille | Colonnes | Usage |
|---|---|---|
| **Ouverture / pré-match** | `odds_home/draw/away` (Pinnacle ouverture), `odds_max_*` (meilleur prix), `odds_open_over25/under25` | **Décision et prix d'entrée** — disponibles AVANT le match, aucune fuite |
| **Clôture** | `odds_close_*` (Pinnacle PSC), `odds_close_over25/under25` | **Mesure a posteriori (CLV) UNIQUEMENT** — jamais en entrée de décision |

La cote de **clôture** intègre toute l'information de marché accumulée jusqu'au
coup d'envoi (compos, blessures de dernière minute, flux d'argent). L'utiliser
pour décider d'un pari serait une fuite manifeste. Elle ne sert qu'à **mesurer**
si notre prix d'entrée était bon (le CLV, voir plus bas). Un test automatisé
verrouille cette règle (`test_find_bets_uses_open_price_not_close`).

## Méthode

- **Walk-forward** identique au backtest RPS : à chaque pli, entraînement sur le
  passé seul, prédiction du bloc suivant jamais vu. 6 plis, départ à 50 % de
  l'historique.
- **Devig** (`pipeline/devig.py`) : retrait de la marge du bookmaker pour obtenir
  des probabilités de marché qui somment à 1 (méthodes proportionnelle et
  *power*). Sert au CLV et aux comparaisons honnêtes.
- **Détection de value** : on parie une issue si `proba_modèle × cote_entrée − 1 > seuil`
  (seuil par défaut 5 %).
- **Mise** : à plat **et** Kelly fractionné (1/4 et 1/2), commission paramétrable,
  suivi de bankroll (ROI, rendement, drawdown max, taux de réussite).
- **Marchés** : 1X2 et Over/Under 2,5 buts (les seuls cotés par football-data).
  BTTS n'a pas de cote chez football-data → traité en calibration ailleurs, pas
  de pari simulé ici.

## Résultats (clubs, 5 grands championnats, seuil d'edge 5 %)

Prix d'entrée = **cote d'ouverture Pinnacle** (apples-to-apples avec la clôture).

### Mise et capital

| Mise | Paris | Rendement (yield) | ROI | Drawdown max |
|---|---|---|---|---|
| À plat (1 u.) | 5 222 | **−8,3 %** | — | — |
| Kelly 1/4 | 5 222 | −12,0 % | −100 % (ruine) | 100 % |
| Kelly 1/2 | 5 222 | −16,5 % | −100 % (ruine) | 100 % |

> Le **rendement** (profit / total misé) est la mesure honnête : **−8,3 %**. La
> ligne « à plat » descend en capital négatif car une mise fixe ignore le risque
> de ruine ; Kelly, lui, dimensionne sur la bankroll et finit logiquement à zéro
> sur une stratégie perdante. Conclusion identique dans les trois cas : **on perd
> de l'argent**.

### Par marché

| Marché | Paris | Rendement | Taux réussite | CLV moyen | Bat la clôture |
|---|---|---|---|---|---|
| 1X2 | 3 383 | −9,2 % | 27,7 % | **−0,53 %** | 46,9 % |
| Over/Under 2,5 | 1 839 | −6,6 % | 41,5 % | **+0,83 %** | 54,0 % |

### Par championnat (1X2 + O/U, mise à plat)

| Ligue | Paris | Rendement | CLV moyen | Bat la clôture |
|---|---|---|---|---|
| Premier League | 1 219 | −3,3 % | +0,21 % | 52,2 % |
| Bundesliga | 903 | −7,6 % | −0,40 % | 47,1 % |
| Serie A | 1 025 | −8,4 % | +0,01 % | 49,2 % |
| Ligue 1 | 831 | −9,9 % | +0,63 % | 53,4 % |
| La Liga | 1 244 | −12,6 % | −0,57 % | 45,9 % |

## CLV — l'indicateur clé d'un edge réel

Le **CLV** (Closing Line Value) compare notre **prix d'entrée** à la **cote de
clôture** : `CLV = cote_entrée / cote_clôture − 1`. Un CLV positif signifie qu'on
a obtenu un meilleur prix que la clôture (« battre la ligne ») — c'est le signe le
plus fiable d'un avantage durable, car la clôture de Pinnacle est réputée la plus
efficiente du marché.

- **Comparaison honnête (même bookmaker, ouverture Pinnacle → clôture Pinnacle)** :
  CLV ≈ **0 sur le 1X2** (−0,05 %, bat la clôture 49,4 % du temps) et **légèrement
  positif sur l'O/U 2,5** (+0,83 %, 54 %). Autrement dit, **notre modèle ne bat
  pas la ligne de clôture sur le 1X2** ; il montre un mince signal sur l'O/U.

- **Au meilleur prix du marché (`odds_max`)** : le CLV devient apparemment positif
  (+2,2 % global, +2,8 % sur le 1X2, bat la clôture ~60 %). **Attention** : c'est
  en grande partie un artefact — comparer le *meilleur* prix parmi tous les
  bookmakers à la clôture d'**un seul** opérateur gonfle mécaniquement le CLV. Et
  même à ce meilleur prix, le **rendement reste négatif (−7,1 %)** : la stratégie
  perd quand même.

## Verdict

> **Le modèle est bien calibré mais ne bat pas le marché.** Rendement négatif sur
> tous les marchés et tous les championnats, CLV nul à négatif sur le 1X2 en
> comparaison loyale. C'est cohérent avec le verdict du backtest RPS de la Phase 3
> (RPS 0,199 vs 0,193 pour le bookmaker).

Ce n'est pas un échec du moteur de backtest — c'est au contraire la preuve qu'il
fonctionne : il dit la vérité, sans complaisance. Pistes honnêtes pour un edge
réel (non garanties) : se concentrer sur l'O/U 2,5 (seul signal positif), ne
parier qu'aux prix les plus élevés du marché en visant un CLV positif loyal,
restreindre aux situations de plus fort désaccord modèle/marché, ou enrichir le
signal (la couche actualité de la Phase 7 vise exactement les écarts d'info de
dernière minute que capte la clôture).

## Vérification

- `python -m pipeline.betting` : backtest complet, sortie JSON (mise à plat +
  Kelly 1/4 et 1/2, ventilation marché et ligue, CLV).
- `tests/test_betting.py` : **18 tests** — devig (somme = 1, marché équitable,
  cotes invalides), maths de Kelly, seuil d'edge, **garde anti-fuite** (la clôture
  n'entre jamais en décision), flat vs Kelly, signe du CLV. **Suite complète : 87
  verts.**
