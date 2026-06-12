"""Lanceur de l'application empaquetée (PyInstaller).

Double-clic -> démarre le serveur local sur un port libre et ouvre le navigateur.
Aucune installation de Python requise pour l'utilisateur final.

En mode empaqueté, la base et les modèles livrés dans le bundle amorcent un dossier
inscriptible (`~/PredictFoot/data`) au premier lancement.
"""

from __future__ import annotations

import socket
import threading
import time
import webbrowser


def _free_port() -> int:
    """Demande au système un port TCP libre."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _open_browser_when_ready(url: str, host: str, port: int) -> None:
    """Ouvre le navigateur dès que le serveur répond (sondage léger)."""
    for _ in range(60):  # ~30 s max
        try:
            with socket.create_connection((host, port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.5)
    webbrowser.open(url)


def main() -> None:
    # Import tardif : laisse le splash/console s'afficher vite.
    from pipeline import config

    # Amorce les données inscriptibles depuis les ressources livrées (1re exécution).
    config.seed_writable_data()

    import uvicorn

    from pipeline.api import app

    host, port = "127.0.0.1", _free_port()
    url = f"http://{host}:{port}/"
    print(f"Prédiction Foot — ouverture de {url}")
    threading.Thread(target=_open_browser_when_ready, args=(url, host, port),
                     daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
