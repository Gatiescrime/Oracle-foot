# Étape 1 — Couche actualité activable en direct (blessures / absences)

## En une phrase

La couche qualitative (qui cherche en ligne les blessures, suspensions, changements
d'entraîneur des deux équipes) est désormais **réellement opérationnelle avec la clé
Anthropic officielle**, **activable depuis l'interface sans toucher au `.env`**, avec des
**garde-fous de coût** visibles.

## Ce qui a été ajouté

### Activation par requête (sans toucher au `.env`)
- L'interrupteur **« Couche actualité (blessures/absences) »** du formulaire envoie
  `use_qualitative: true/false` à `POST /api/predict`. **OFF par défaut.**
- Côté code : `QualitativeLayer.adjust(..., enabled_override=…)` — `True` force
  l'exécution, `False` la désactive, `None` retombe sur la valeur du `.env`. Le champ
  `qualitative_enabled` renvoyé reflète l'état **effectif de la requête**, donc l'UI
  affiche toujours le bon panneau (active / sans actu / désactivée).

### Garde-fous de coût
- **Cache TTL 12 h** : un même match n'est ré-interrogé qu'au-delà du TTL (déjà en place).
- **Plafond de recherches web par match** : `max_uses = 5` (déjà en place).
- **Compteur d'appels du jour** : nouvelle table `api_usage(day, calls)`, incrémentée à
  chaque vrai appel LLM (extraction + synthèse). Exposé par `GET /api/qualitative/status`
  et **affiché dans l'UI** à côté de l'interrupteur, avec l'avertissement
  *« consomme l'API officielle (coût réel) »*.

### Endpoint d'état
`GET /api/qualitative/status` → `{enabled_default, calls_today, cache_ttl_hours,
max_web_uses, news_window_days}`.

## Correctifs nécessaires découverts au test live

1. **URL de base vide** : `load_dotenv(override=True)` écrasait la variable shell
   `ANTHROPIC_BASE_URL` par la chaîne vide du `.env`. Le SDK lisait alors cette chaîne
   vide et fabriquait une URL invalide (`httpx UnsupportedProtocol`). Corrigé en
   **retirant la variable d'environnement** quand elle est vide → le SDK utilise son
   endpoint officiel par défaut (`api.anthropic.com`).
2. **Identifiants de modèle** : `claude-haiku-4.5` / `claude-sonnet-4.6` (avec points)
   n'existent pas côté API → `404 not_found_error`. Remplacés par les identifiants réels
   `claude-haiku-4-5-20251001` et `claude-sonnet-4-6`.

## Test LIVE (vérification réelle, clé officielle)

Lancé sur **France – Brésil (FIFA World Cup)**, le 2026-06-12 (Coupe du Monde en cours) :

```json
{
  "mult_dom": 1.02,
  "mult_ext": 0.97,
  "facteurs": [
    "Mbappé blessure cuisse incertaine affaiblit attaque France légèrement",
    "Wesley remplacé par Éderson affaiblit légèrement l'aile brésilienne"
  ],
  "faits": [
    {"fait": "Wesley retiré du groupe pour blessure, remplacé par Éderson",
     "equipe": "Brazil", "date": "2026-06-07",
     "source_url": "https://en.wikipedia.org/wiki/Brazil_national_football_team"},
    {"fait": "Mbappé menace d'une blessure à la cuisse",
     "equipe": "France", "date": "2026-06-08",
     "source_url": "https://sports.yahoo.com/articles/france-football-team-2026-..."},
    {"fait": "France conclut ses amicaux par une victoire 3-1 contre l'Irlande du Nord",
     "equipe": "France", "date": "2026-06-10",
     "source_url": "https://www.fourfourtwo.com/team/france-world-cup-2026-squad"}
  ],
  "confiance": 0.35,
  "source": "claude+web"
}
```

**Vérifié :**
- `web_search` **s'exécute réellement** (blocs `server_tool_use` + `web_search_tool_result`
  dans la réponse) ;
- les **faits sont datés (< 7 jours)** et portent une **URL de source** ;
- l'**ajustement reste borné à ±25 %** (1.02 et 0.97) ;
- le LLM **ne prédit jamais le score** (il ne renvoie que des faits + multiplicateurs) ;
- hors-saison clubs (12 juin), une recherche Real Madrid–Barcelone renvoie `[]` proprement
  (aucun match programmé) → **aucun ajustement**, pas de fausse information ;
- réseau/API KO → `None` propre, **zéro crash** (testé).

## Conformité aux règles d'or

- **Le LLM extrait des faits, ne prédit pas le score.** ✔
- **Secret jamais exposé au frontend** : la clé reste côté serveur ; l'UI ne voit que des
  faits, des multiplicateurs et un compteur. ✔
- **OFF par défaut**, activation explicite et avertie du coût. ✔
- Cette couche **ne se backteste pas** (pas d'historique de news horodaté) : validation
  prospective uniquement.

## Tests

`tests/test_qualitative.py` : `enabled_override` (force on/off), compteur d'appels
(incrément sur cache-miss, pas de double comptage sur cache frais).
`tests/test_service_api.py` : prédiction OFF = socle statistique seul, toggle ON via
faux client, endpoint `/api/qualitative/status`. **Suite complète : 55 verts.**
