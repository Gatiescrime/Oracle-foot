#!/usr/bin/env bash
# Lance le serveur de prédiction et ouvre le navigateur.
# Usage : ./run.sh   (entraîne les modèles s'ils manquent, puis démarre l'app)
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
PORT="${PORT:-8000}"

if [ ! -x "$PY" ]; then
  echo "Environnement Python introuvable (.venv). Crée-le puis : pip install -r requirements.txt"
  exit 1
fi

# Entraîne les modèles au premier lancement (artefacts dans data/models/).
if [ ! -f data/models/meta_club.json ] || [ ! -f data/models/meta_intl.json ]; then
  echo "→ Premier lancement : entraînement des modèles…"
  "$PY" -m pipeline.train
fi

URL="http://127.0.0.1:${PORT}/"
echo "→ Démarrage du serveur sur ${URL}"
( sleep 2; (open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || true) ) &

exec "$PY" -m uvicorn pipeline.api:app --host 127.0.0.1 --port "$PORT"
