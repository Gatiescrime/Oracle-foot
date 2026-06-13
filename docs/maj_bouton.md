# Consolidation — Étape 1 : réparer le bouton « Mettre à jour »

## Le problème

Le mode « Rapide » appelait `refresh.refresh(use_cache=False)` : il re-téléchargeait
**toutes** les sources, y compris understat (xG + buts par joueur, ~50 requêtes lentes).
Sans **aucun timeout global**, le job pouvait traîner des minutes ou rester bloqué — le
bouton semblait « planté ».

## La correction

### 1. Rapide = vraiment rapide (réutilisation du cache)
`refresh.refresh(use_cache, quick=False)` gagne un mode rapide :
- **football-data** (résultats) et **martj42** (sélections) → re-téléchargés (légers) ;
- **understat** (xG + joueurs, le goulot) → **réutilisé depuis le cache disque**, invalidé
  seulement au-delà de `QUICK_HEAVY_TTL_HOURS` (24 h) ;
- **timeout réseau court par source** en rapide (`QUICK_HTTP_TIMEOUT`=8 s, 1 essai) :
  une source qui traîne échoue franchement au lieu de bloquer.

> Mesuré : une mise à jour rapide complète passe de « minutes / blocage » à **~8 s**
> (cache understat chaud).

### 2. Délai maximum DUR (jamais bloqué)
`refresh_job.run` pilote désormais le travail dans un **thread** et attend avec un
**timeout global dur** (`REFRESH_TIMEOUT_QUICK_S`=25 s, `REFRESH_TIMEOUT_FULL_S`=300 s).
Au-delà → état **`error`** propre avec message clair (« Délai dépassé… »), le job ne peut
**jamais** rester bloqué. Un compteur de **génération** garantit qu'un thread abandonné
(après dépassement) ne réécrit pas l'état d'un job plus récent.

### 3. Bouton « Annuler »
La fenêtre a un bouton **Annuler** qui **stoppe le suivi** (`clearInterval`) et **referme
proprement** (réactive « Lancer », masque la barre de progression). La tâche de fond
éventuelle se termine ou expire seule côté serveur grâce au timeout dur.

### 4. Inchangé
Le **compteur de secondes** et les **messages par mode** (rapide / complet) sont
conservés ; les échecs réseau affichent un message explicite.

## Réglages (env, valeurs par défaut)

| Variable | Défaut | Rôle |
|---|---|---|
| `QUICK_HEAVY_TTL_HOURS` | 24 | Âge max du cache understat réutilisé en rapide |
| `QUICK_HTTP_TIMEOUT` | 8 s | Timeout réseau par source en rapide |
| `QUICK_HTTP_RETRIES` | 1 | Essais réseau par source en rapide |
| `REFRESH_TIMEOUT_QUICK_S` | 25 s | Timeout global dur du job rapide |
| `REFRESH_TIMEOUT_FULL_S` | 300 s | Timeout global dur du job complet (réentraînement) |

## Tests

`tests/test_refresh_job.py` (10 tests verts) :
- `test_quick_mode_uses_quick_refresh` / `test_full_mode_is_not_quick` : le bon mode
  est propagé à `refresh.refresh(quick=…)` ;
- `test_global_timeout_never_blocks` : une source qui dépasse le délai → état `error`
  « Délai dépassé », jamais de blocage ;
- les tests existants (machine à états, verrou, repli erreur, endpoints) restent verts.

**Suite complète : 159 tests verts.**

## Critère d'acceptation — atteint

✅ Rapide < ~15 s grâce au cache (mesuré ~8 s)
✅ Aucune mise à jour ne peut rester bloquée (timeout global dur → `error` propre)
✅ Messages clairs en cas d'échec réseau ; bouton Annuler qui referme proprement
