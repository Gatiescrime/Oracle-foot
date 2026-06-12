# Étape 6 — Page « Track record »

## L'idée, en une phrase

Un onglet qui présente **honnêtement les performances réelles du modèle**, à partir
des chiffres déjà calculés par le backtest walk-forward — sans rien inventer.

## Comment ça marche

- Nouvel endpoint `GET /api/track-record` qui lit `data/backtest_result.json`
  (produit par `python -m pipeline.backtest`). Renvoie `available=False` proprement
  si l'artefact n'existe pas encore.
- **Aucun chiffre inventé** : tout vient du JSON du backtest chronologique (6 plis,
  entraînement uniquement sur le passé).
- Le frontend affiche, par domaine :
  - **Clubs** : nombre de prédictions hors échantillon, RPS modèle (calibré),
    RPS bookmaker (l'étalon à battre), écart au bookmaker (en rouge), et le ROI du
    value betting simulé.
  - **Sélections** : prédictions, RPS, log-loss ; mention claire que le marché n'est
    **pas comparable** (pas de cotes internationales dans nos sources).
  - **Calibration** : pour chaque tranche (effectif ≥ 100), « le modèle annonce X % →
    ça arrive Y % », avec mise en avant des tranches bien calibrées (écart ≤ 3 pts).

## Chiffres réels affichés (état actuel)

| Domaine | Prédictions | RPS modèle | RPS bookmaker | Verdict |
|---|---|---|---|---|
| Clubs | 4 454 | 0,201 | **0,193** | bookmaker devant ; value betting **ROI −7,6 %** |
| Sélections | 24 703 | 0,188 | indispo. | solide, non comparable au marché |

## Honnêteté assumée

La page le dit sans détour : **le modèle ne bat pas encore le bookmaker** sur les clubs
et la stratégie de paris à la valeur perd de l'argent sur l'historique. Sa force est sa
**calibration quasi parfaite** : quand il annonce X %, l'événement arrive ~X % du temps.
C'est cohérent avec le verdict du backtest (mémoire projet) et avec la note de bas de
page (« ce n'est pas un conseil financier »).

## Tests

`tests/test_service_api.py::test_track_record_endpoint_real_numbers` : l'endpoint
expose `available`, et quand l'artefact est présent, des RPS plausibles (0–1),
le bloc `value_betting`, la courbe de calibration et les chiffres des deux domaines.

**Suite complète verte : 155 tests.**

## Critère d'acceptation — atteint

✅ Un onglet présente les vraies performances (RPS, calibration, ROI/value, écart au marché)
✅ Chiffres issus du backtest chronologique déjà calculé, jamais inventés
✅ Note explicative honnête (le modèle ne bat pas le marché, mais est bien calibré)

## Où ça vit

| Élément | Fichier |
|---|---|
| Lecture de l'artefact backtest | `pipeline/service.py` (`track_record`) |
| Endpoint | `pipeline/api.py` (`GET /api/track-record`) |
| Onglet + vue + rendu | `webapp/index.html` (`#view-track`), `webapp/app.js` (`loadTrackRecord`, `renderTrackRecord`, `calibrationTable`) |
| Style | `webapp/style.css` (`.track-note`, `.track-sub`) |
| Source des chiffres | `data/backtest_result.json`, `docs/performance.md` |
