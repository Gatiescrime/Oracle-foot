# syntax=docker/dockerfile:1
# Image multi-étapes : on installe les dépendances dans un étage "builder",
# puis on copie uniquement le nécessaire dans une image finale légère.
# La base (data/football.db) et les modèles (data/models/) sont EMBARQUÉS :
# l'app démarre et prédit sans aucun accès réseau.

# --- étage 1 : dépendances -------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /install

# Outils de compilation au cas où une roue manque (xgboost/scipy livrent des wheels).
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix=/install/deps -r requirements.txt

# --- étage 2 : image finale ------------------------------------------------
FROM python:3.12-slim AS runtime

# libgomp1 : requis à l'exécution par XGBoost (OpenMP).
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    QUALITATIVE_LAYER_ENABLED=false \
    PORT=8000

WORKDIR /app

# Dépendances Python installées à l'étage précédent.
COPY --from=builder /install/deps /usr/local

# Code applicatif + ressources livrées (base, modèles, frontend).
COPY pipeline/ ./pipeline/
COPY webapp/ ./webapp/
COPY data/football.db ./data/football.db
COPY data/models/ ./data/models/
# Résultat du backtest (alimente la page Track record) + données features livrées.
COPY data/backtest_result.json ./data/backtest_result.json
COPY data/squad_values.csv ./data/squad_values.csv
COPY data/venues.csv ./data/venues.csv

EXPOSE 8000

# Render (et la plupart des hébergeurs) fournit le port via $PORT.
# Forme shell pour que la variable soit développée au démarrage.
CMD uvicorn pipeline.api:app --host 0.0.0.0 --port ${PORT:-8000}
