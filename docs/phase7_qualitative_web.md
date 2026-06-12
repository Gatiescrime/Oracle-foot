# Phase 7 — La couche qualitative consciente de l'actualité

## Le problème que cette phase corrige

En Phase 5, `pipeline/qualitative.py` appelait Claude **sans accès au web**. Le modèle
se reposait sur ses connaissances d'entraînement : il ne pouvait donc **pas** percevoir
une blessure survenue hier. La Phase 7 fait que l'étape d'extraction va **vraiment
chercher l'info récente en ligne**.

## Ce qui change

### 1. Recherche web réelle pendant l'extraction
L'appel d'extraction (modèle Haiku) embarque désormais l'outil **`web_search`** de l'API
Anthropic (via le proxy). Le modèle cherche l'actualité des **7 derniers jours** des deux
équipes : indisponibilités (blessure / suspension) avec nom + raison, changement
d'entraîneur, enjeu, fatigue calendaire.

### 2. Chaque fait est daté et sourcé
L'extraction renvoie une **liste JSON** de faits, chacun portant :
`fait`, `equipe`, `date` (AAAA-MM-JJ), `source_titre`, `source_url`. On **ne garde que**
les faits datés dans la fenêtre récente **et** munis d'une URL `http(s)` vérifiable ; tout
le reste est ignoré (`_filter_recent`).

### 3. Repli si le proxy ne relaie pas `web_search`
On détecte l'absence d'appel d'outil dans la réponse (`_used_web_search`). Dans ce cas,
ou si l'appel échoue, on **logue clairement** puis on tente un **fournisseur de recherche
d'actu configurable** par `.env` :

```
NEWS_SEARCH_URL=...      # API JSON : ?q=...&days=... -> {"results":[{title,url,date,snippet}]}
NEWS_SEARCH_API_KEY=...
NEWS_SEARCH_PROVIDER=... # libellé informatif (affiché comme source)
```

Si aucun fournisseur n'est configuré → **aucun ajustement** (jamais de crash).

### 4. Cache avec TTL
La clé reste `compétition|dom|ext|date`, mais comme l'actu évolue on ajoute un **TTL**
(`QUALITATIVE_CACHE_TTL_HOURS`, 12 h par défaut) : au-delà, on **réinterroge**. Si le
réseau est KO et qu'une entrée périmée existe, on la **sert quand même** (filet de
secours).

### 5. Transparence
L'objet d'ajustement renvoie maintenant `faits: [{fait, equipe, date, source_titre,
source_url}]` en plus de `facteurs`, `mult_dom`, `mult_ext`, `confiance`, `source`
(`claude+web` ou `claude+<fournisseur>`) et `as_of`. L'UI affiche chaque fait avec sa
**date** et un **lien cliquable** vers la source.

## Garde-fous conservés (non négociables)

- Ajustement **borné à ±25 %** (`_clamp`), y compris face à des valeurs extrêmes / `NaN`.
- **Off par défaut** (`QUALITATIVE_LAYER_ENABLED`).
- Le LLM **extrait des faits**, ne donne **jamais** de pronostic, de probabilité ni de score.
- **Zéro crash** : toute panne (réseau, outil, parsing) → repli propre, au pire aucun
  ajustement et le modèle statistique tourne seul.

## Réglages `.env` (Phase 7)

| Variable | Défaut | Rôle |
|---|---|---|
| `QUALITATIVE_WEB_SEARCH_ENABLED` | `true` | active l'outil web_search à l'extraction |
| `QUALITATIVE_WEB_SEARCH_TOOL` | `web_search_20250305` | type d'outil web de l'API |
| `QUALITATIVE_WEB_SEARCH_MAX_USES` | `5` | nb max de recherches par extraction |
| `QUALITATIVE_NEWS_WINDOW_DAYS` | `7` | fenêtre de fraîcheur des faits |
| `QUALITATIVE_CACHE_TTL_HOURS` | `12` | au-delà, on réinterroge |
| `NEWS_SEARCH_URL` / `NEWS_SEARCH_API_KEY` / `NEWS_SEARCH_PROVIDER` | `""` | fournisseur de repli |

## Note sur le proxy actuel (constat en conditions réelles)

Testé en direct, le proxy configuré dans `.env` (`api.vibecode-claude.online`) **accepte**
le paramètre `tools` sans erreur mais **n'exécute pas** `web_search` (aucun appel d'outil
dans la réponse) ; le modèle derrière se comporte par ailleurs comme un assistant orienté
code. Conséquence concrète : via **ce** proxy, la couche bascule proprement sur le repli
et **ne produit aucun ajustement** (loggé, zéro crash) — exactement le comportement voulu.

Pour que la couche s'appuie réellement sur l'actu, il faut **soit** un point d'accès qui
relaie l'outil `web_search` de l'API Anthropic, **soit** configurer le fournisseur de news
de repli (`NEWS_SEARCH_URL`/`NEWS_SEARCH_API_KEY`). La logique, elle, est validée par les
tests (recherche web mockée).

> Correctif transverse apporté ici : `config.py` charge désormais le `.env` du projet avec
> `override=True`. Sans cela, des variables `ANTHROPIC_*` déjà exportées par le shell
> (cas de Claude Code) masquaient la clé et l'URL du proxy → toute la couche LLM échouait
> silencieusement à s'authentifier.

## Honnêteté

Comme en Phase 5, cette couche **ne se backteste pas** (pas d'historique de news
horodaté aligné sur chaque match passé). Sa validation reste **prospective**. Elle reste
**off par défaut** tant qu'elle n'a pas fait ses preuves en conditions réelles.

## Critère d'acceptation (tests `tests/test_qualitative.py`, 10 verts)

- Couche ON, match d'actualité : l'ajustement s'appuie sur des faits datés **< 7 jours**
  avec **URL** ; la blessure d'un cadre **abaisse, dans la borne**, les buts attendus de
  son équipe ; le fait + la source sont **visibles** dans la réponse
  (`test_facts_are_recent_sourced_and_used`).
- Faits hors fenêtre (30 j) → **filtrés**, aucun ajustement (`test_old_facts_are_filtered_out`).
- `web_search` non relayé et aucun fournisseur → **None propre**, zéro crash
  (`test_web_not_relayed_and_no_provider_returns_none`).
- Cache frais servi sans réseau ; cache périmé → réinterrogation
  (`test_fresh_cache_served_without_network`, `test_stale_cache_refreshes`).
- Couche OFF → prédiction **strictement identique** (`test_prediction_identical_when_off`).
- Aucun appel réseau réel : faux client Anthropic + recherche web mockée.
