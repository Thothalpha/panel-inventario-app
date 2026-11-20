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


def _setup_tls(log):
    """El Python empaquetado no ve los certificados del sistema: sin esto, HTTPS falla con
    CERTIFICATE_VERIFY_FAILED. Se usa el almacén de confianza de macOS (truststore) y, si no
    está disponible, la lista de certifi."""
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except Exception as e:  # pragma: no cover
        log.warning("certifi no disponible: %s", e)
    try:
        import truststore
        truststore.inject_into_ssl()
        return "truststore"
    except Exception as e:
        log.warning("truststore no disponible (%s); se usa certifi", e)
        return "certifi"


def main():
    data_dir, log_dir = _dirs()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["PANEL_DATA_DIR"] = str(data_dir)
    token = secrets.token_urlsafe(32)
    os.environ["PANEL_APP_TOKEN"] = token
    _setup_logging(log_dir)
    log = logging.getLogger("desktop")
    tls = _setup_tls(log)
    log.info("Certificados TLS: %s", tls)

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
        # Documentos y skills dentro del paquete: openpyxl, pypdf y fpdf2 empaquetados, skills integradas presentes
        from app import files, skills
        assert skills.all_skills(), "no se han empaquetado las skills"
        x = files.create_excel("selftest", [{"columnas": ["Ref", "Stock"], "filas": [["A-1", 3]]}], origin="selftest")
        pdf = files.create_pdf("selftest", "Selftest", [{"tipo": "tabla", "columnas": ["Ref", "€"], "filas": [["A-1", 1]]}])
        assert files.read(x["id"])["filas"] and files.read(pdf["id"])["paginas"], "documentos ilegibles"
        files.delete(x["id"]); files.delete(pdf["id"])
        # HTTPS real con la configuración de certificados de la app (Odoo, OpenRouter y GitHub lo usan)
        import ssl
        import xmlrpc.client
        urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=20).read(100)
        from app.odoo_client import _proxy
        try:
            _proxy("https://www.odoo.com/xmlrpc/2/common").version()
        except xmlrpc.client.Fault:
            pass  # responde Odoo: el TLS ha funcionado
        except ssl.SSLError as e:
            raise SystemExit(f"SELFTEST FALLO TLS: {e}")
        print(f"SELFTEST OK · versión {info['version']} · configurada={info['configured']} · tls={tls} · llavero="
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
