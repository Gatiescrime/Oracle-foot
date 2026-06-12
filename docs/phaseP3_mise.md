# Phase P3 — De la prédiction à une mise recommandée

## L'objectif, en une phrase

Transformer une probabilité du modèle **+** le prix (la cote) que propose votre
bookmaker en une **recommandation de mise concrète** : y a-t-il un avantage ?
Combien miser, prudemment ? Et surtout : *quand ne PAS parier*.

C'est un outil **informatif**, pas un conseil de pari. Le rappel est partout.

---

## Les trois chiffres qui comptent

Pour une issue (victoire domicile, match nul, plus de 2,5 buts, etc.) :

1. **L'edge (avantage)** = `proba_modèle × cote − 1`.
   - Positif → le modèle pense que la cote est trop généreuse (à notre avantage).
   - Négatif ou nul → la cote ne paie pas assez : aucun intérêt.
   - Exemple : modèle = 60 %, cote = 2,00 → edge = `0,60 × 2,00 − 1 = +0,20` (+20 %).

2. **L'espérance de gain par unité misée** = `p·(cote−1)·(1−commission) − (1−p)`.
   C'est le gain moyen attendu pour 1 € misé. La commission (paris en bourse)
   la réduit ; par défaut elle est à 0.

3. **La probabilité implicite du marché** = `1 / cote`. C'est ce que « pense » le
   bookmaker (marge incluse). On l'affiche pour comparer d'un coup d'œil avec la
   proba du modèle.

---

## La mise : Kelly fractionné, plafonné

Le **critère de Kelly** donne la fraction du capital qui maximise la croissance
à long terme :

```
f* = (b·p − q) / b      avec b = cote − 1, q = 1 − p
```

Kelly « plein » est **trop agressif** dans la vraie vie (nos probabilités sont
imparfaites, la variance est brutale). On applique donc deux garde-fous :

- **Fraction de Kelly** : on ne mise qu'un **quart** de Kelly (`BET_KELLY_FRACTION = 0.25`).
- **Plafond dur** : jamais plus de **5 % du capital** sur un seul pari
  (`BET_MAX_STAKE_FRAC = 0.05`), quoi que dise Kelly.

```
mise = min( 0,25 × f*,  0,05 ) × capital      (et jamais < 0)
```

Exemple : modèle 60 %, cote 2,00, capital 100 €.
`f* = 0,20` → quart de Kelly = `0,05` → sous le plafond 5 % → **mise = 5 €**.

Gros edge (modèle 90 %, cote 3,00, demi-Kelly) : Kelly voudrait beaucoup, mais
le plafond 2 % (si configuré) ramène la mise à 2 % du capital. La borne gagne
**toujours**.

---

## Le seuil de value (anti-bruit)

On ne déclenche une recommandation que si l'edge dépasse un **seuil**
(`BET_EDGE_THRESHOLD = 0.05`, soit +5 %). En dessous, l'avantage est trop fragile
face à l'incertitude du modèle : la réponse est **« pas de value, déconseillé »**,
et la mise est **0**.

---

## Garde-fous toujours affichés

Chaque réponse embarque trois avertissements, non négociables :

- « Probabilités indicatives, à but informatif : **aucune garantie de gain**. »
- « Ne misez que ce que vous pouvez vous permettre de **perdre**. »
- « Forte **variance** : même un pari à valeur positive peut perdre, souvent
  plusieurs fois de suite. »

Et quand il n'y a pas de value, le message le dit franchement et **n'incite à
rien**.

---

## Où ça vit dans le code

| Élément | Fichier |
|---|---|
| Logique pure (Kelly, edge, EV, bornes, garde-fous) | `pipeline/staking.py` |
| Branchement modèle → issue → mise | `service.recommend_stake()` |
| Endpoint HTTP | `POST /api/stake` |
| Panneau UI (cote, capital, issue → résultat) | `webapp/` (bloc `#stake`) |
| Réglages | `config.py` : `BET_EDGE_THRESHOLD`, `BET_KELLY_FRACTION`, `BET_MAX_STAKE_FRAC`, `BET_COMMISSION` |

`POST /api/stake` reçoit `{competition, home, away, selection, odds, bankroll}`,
calcule la proba du modèle pour l'issue (même pipeline que la prédiction, couche
actualité incluse si activée), puis renvoie edge, value, mise et garde-fous.

---

## Tests (`tests/test_staking.py`, 10 cas)

- Kelly : nul sans edge, positif avec edge, jamais négatif.
- Recommandation : pas de value → mise 0 + message « déconseillé ».
- Value → mise = quart de Kelly tant qu'on est sous le plafond.
- Plafond : un gros edge est **toujours** ramené au cap (ex. 2 %).
- Entrées invalides (cote ≤ 1, capital nul, proba absurde) → `valid = False`.
- Garde-fous toujours présents (≥ 3, dont « aucune garantie » et « perdre »).
- La commission réduit bien l'espérance.
- Bout en bout sur l'endpoint si les modèles sont entraînés (mise bornée à 5 %).

Suite complète : **105 tests au vert**.

---

## Rappel honnête (lien avec P1/P2)

Le backtest des phases P1/P2 a montré que **le modèle ne bat pas le marché** sur
le 1/X/2 (rendement négatif, CLV ≈ 0). Cet outil est donc une **mécanique de
mise correcte** posée sur un signal qui, aujourd'hui, n'a pas d'avantage prouvé
contre les bookmakers. Il calcule proprement *combien miser SI* il y a value —
il ne crée pas la value. Conforme aux règles d'or : aucune promesse, tout est dit.
