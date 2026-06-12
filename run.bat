@echo off
REM Lance le serveur de prédiction et ouvre le navigateur (Windows).
REM Usage : double-cliquer run.bat (entraîne les modèles s'ils manquent, puis démarre).
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not defined PORT set "PORT=8000"

if not exist "%PY%" (
  echo Environnement Python introuvable (.venv).
  echo Cree-le puis : pip install -r requirements.txt
  pause
  exit /b 1
)

REM Entraine les modeles au premier lancement (artefacts dans data\models\).
if not exist "data\models\meta_club.json" (
  echo -^> Premier lancement : entrainement des modeles...
  "%PY%" -m pipeline.train
)

set "URL=http://127.0.0.1:%PORT%/"
echo -^> Demarrage du serveur sur %URL%
start "" "%URL%"

"%PY%" -m uvicorn pipeline.api:app --host 127.0.0.1 --port %PORT%
endlocal
