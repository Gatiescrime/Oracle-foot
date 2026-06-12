# -*- mode: python ; coding: utf-8 -*-
# Spécification PyInstaller : un exécutable autonome (Windows .exe / macOS .app).
# Embarque le frontend, les modèles entraînés et la base de données.
#
# Build : pyinstaller packaging/app.spec   (depuis la racine du dépôt)

import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.getcwd())

# Ressources embarquées : (source, destination dans le bundle).
datas = [
    (os.path.join(ROOT, "webapp"), "webapp"),
    (os.path.join(ROOT, "data", "models"), os.path.join("data", "models")),
    (os.path.join(ROOT, "data", "football.db"), "data"),
]

# Imports dynamiques que PyInstaller ne détecte pas seul.
hiddenimports = (
    collect_submodules("xgboost")
    + collect_submodules("sklearn")
    + collect_submodules("scipy")
    + collect_submodules("statsmodels")
    + collect_submodules("uvicorn")
    + collect_submodules("anthropic")
    + ["pipeline.api", "fastapi", "pydantic"]
)

block_cipher = None

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PredictionFoot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,        # pas de fenêtre console (app fenêtrée)
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# macOS : produire aussi un bundle .app
app = BUNDLE(
    exe,
    name="PredictionFoot.app",
    icon=None,
    bundle_identifier="com.predictionfoot.app",
)
