# Phase 5 — La couche qualitative (actualité)

## En une phrase

Une couche **optionnelle et bornée** qui lit l'actualité récente d'un match (blessures,
suspensions, changement d'entraîneur, enjeu) via un modèle Claude, et en déduit un
**petit** ajustement des buts attendus — plafonné à ±25 %. Le socle statistique reste
maître ; sans cette couche, le système tourne exactement comme avant.

## Pourquoi cette règle d'or : « le LLM ne prédit pas le score »

Un modèle de langage n'a aucune compétence pour estimer une probabilité de victoire.
Il est en revanche bon pour **résumer des faits récents**. On l'utilise donc uniquement
comme un capteur d'actualité, jamais comme un pronostiqueur. La décision chiffrée reste
au modèle Dixon-Coles + XGBoost calibré.

## Le pipeline en deux temps (`pipeline/qualitative.py`)

1. **Extraction** (modèle Haiku, bon marché) : on demande une liste de faits concrets et
   vérifiables. Consigne explicite : *aucun pronostic, aucune probabilité, aucun score.*
2. **Synthèse** (modèle Sonnet) : on transforme ces faits en deux multiplicateurs
   (domicile / extérieur) centrés sur 1.0, des facteurs lisibles, et une confiance,
   au format JSON strict.

L'ajustement est appliqué multiplicativement sur les buts attendus (λ domicile, μ
extérieur), puis la matrice des scores est recalculée. `<1.0` = équipe affaiblie,
`>1.0` = équipe renforcée.

## Les garde-fous (non négociables)

- **Désactivée par défaut** : flag `.env QUALITATIVE_LAYER_ENABLED` (false). Le système
  ne dépend jamais d'elle.
- **Bornée** : tout multiplicateur est ramené dans `[0.75, 1.25]` (±25 %), y compris
  face à des valeurs extrêmes ou à du `NaN` (`_clamp`).
- **Sans crash** : la moindre erreur (réseau, parsing JSON, panne API) fait retomber
  proprement sur « aucun ajustement » → on rejoue le modèle statistique seul.
- **Mise en cache** : table `news_cache` (clé = compétition|dom|ext|date). Conservée
  même après un rafraîchissement des données.
- **Affichée et justifiée** : l'UI montre les multiplicateurs, les facteurs et la
  confiance. L'utilisateur voit toujours *pourquoi* un ajustement a eu lieu.

## Honnêteté

Cette couche **ne se backteste pas** : il n'existe pas d'historique de news horodaté
aligné sur chaque match passé. Sa validation ne peut être que prospective. C'est la
raison pour laquelle elle reste **off par défaut** tant qu'elle n'a pas fait ses preuves
en conditions réelles.

## Critère d'acceptation (tests `tests/test_qualitative.py`)

- Couche OFF → prédiction **strictement identique** au modèle statistique seul.
- Couche ON → ajustement borné (±25 %), justifié (facteurs présents), et visible dans
  la sortie.
- Aucune dépendance réseau dans les tests : on injecte un faux client Anthropic.
- Une panne du client → retour `None`, jamais de crash.
