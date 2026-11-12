"""Punto de entrada de la app de escritorio (macOS).

1. Carpetas: datos en ~/Library/Application Support/PanelInventario y registro en
   ~/Library/Logs/PanelInventario (en Linux, ~/.local/share y ~/.cache para desarrollo).
2. Servidor FastAPI en 127.0.0.1 con un puerto libre y un token aleatorio por sesión.
3. Ventana nativa (pywebview → WebKit de macOS). Al cerrarla, la app termina.
"""

import logging
import logging.handlers
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_NAME = "PanelInventario"


def _dirs():
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support" / APP_NAME, home / "Library/Logs" / APP_NAME
    return home / ".local/share" / APP_NAME, home / ".cache" / APP_NAME / "logs"


def _setup_logging(log_dir: Path):
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_dir / "app.log", maxBytes=2_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # Las librerías HTTP no deben registrar cabeceras (podrían incluir claves)
    for noisy in ("httpx", "httpcore", "openai", "anthropic", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(url, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def main():
    data_dir, log_dir = _dirs()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["PANEL_DATA_DIR"] = str(data_dir)
    token = secrets.token_urlsafe(32)
    os.environ["PANEL_APP_TOKEN"] = token
    _setup_logging(log_dir)
    log = logging.getLogger("desktop")

    # Importar después de fijar las variables de entorno (config las lee al importarse)
    import uvicorn
    import webview

    from app import config
    from app.main import app

    log.info("Arranque %s %s · datos en %s", APP_NAME, config.APP_VERSION, data_dir)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           access_log=False, log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    if not _wait_ready(base + "/static/styles.css"):
        log.error("El servidor interno no arrancó")
        sys.exit(1)

    if "--selftest" in sys.argv:
        # Comprobación sin ventana (CI): el paquete arranca, sirve la interfaz y la API responde con token
        import json
        req = urllib.request.Request(base + "/api/setup", headers={"X-App-Token": token})
        info = json.loads(urllib.request.urlopen(req, timeout=10).read())
        html = urllib.request.urlopen(base + "/", timeout=10).read().decode("utf-8")
        assert "setup-form" in html, "index.html incompleto"
        print(f"SELFTEST OK · versión {info['version']} · configurada={info['configured']} · llavero="
              f"{type(__import__('keyring').get_keyring()).__name__}")
        server.should_exit = True
        return

    webview.create_window(f"Panel de inventario {config.APP_VERSION}", f"{base}/#t={token}",
                          width=1380, height=880, min_size=(980, 640), text_select=True)
    webview.start(private_mode=False, storage_path=str(data_dir / "webview"))
    server.should_exit = True
    log.info("Cierre")


if __name__ == "__main__":
    main()
