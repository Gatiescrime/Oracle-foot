PY = .venv/bin/python

.PHONY: run refresh features train backtest test

run:        ## Entraîne si besoin puis lance l'app web
	./run.sh

refresh:    ## Reconstruit la base de données depuis les sources
	$(PY) -m pipeline.refresh

features:   ## (Re)calcule les features sans fuite
	$(PY) -m pipeline.features

train:      ## Entraîne et exporte les modèles dans data/models/
	$(PY) -m pipeline.train

backtest:   ## Backtest chronologique (RPS, calibration, vs bookmakers)
	$(PY) -m pipeline.backtest

test:       ## Lance la suite de tests
	$(PY) -m pytest -q
