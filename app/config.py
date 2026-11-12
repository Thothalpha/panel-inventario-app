"""Configuración: rutas, versión, variables de entorno y parámetros de negocio editables (settings.json).

Copyright (C) 2026 Thothalpha · Licencia AGPL-3.0 (ver LICENSE)
"""

import json
import os
import sys
from pathlib import Path

APP_NAME = "PanelInventario"
APP_VERSION = "0.1.0"
GITHUB_REPO = "Thothalpha/panel-inventario-app"

# Recursos (static/): dentro del paquete de PyInstaller viven en sys._MEIPASS
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
FROZEN = bool(getattr(sys, "frozen", False))

# Datos del usuario: en la app de macOS, ~/Library/Application Support/PanelInventario
DATA_DIR = Path(os.environ.get("PANEL_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"

# En desarrollo, importar el cliente carga el .env del proyecto en os.environ
from . import odoo_client  # noqa: E402,F401

DEFAULT_SETTINGS = {
    # Previsión / reaprovisionamiento
    "plazo_entrega_defecto_dias": 21,      # si el producto no tiene plazo de proveedor en Odoo
    "nivel_servicio": 0.95,                # para el stock de seguridad (z)
    "periodo_revision_dias": 30,           # cada cuánto se revisa/compra
    "semanas_historia": 104,
    "horizonte_semanas": 13,
    # Umbrales de alertas automáticas
    "exceso_cobertura_dias": 365,
    "exceso_valor_min": 500,               # € mínimos para avisar de exceso
    "sin_movimiento_dias": 180,
    "caducidad_aviso_dias": 90,
    "anomalia_z": 2.5,
    "ack_dias": 7,                         # una alerta "vista" vuelve a salir tras N días
    # ABC / XYZ
    "abc_a": 0.80,
    "abc_b": 0.95,
    "xyz_x": 0.5,
    "xyz_y": 1.0,
    # Contexto que se da al asistente sobre la empresa (productos, familias de referencias…)
    "contexto_empresa": "",
}


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return s


def save_settings(new: dict) -> dict:
    s = load_settings()
    for k, v in new.items():
        if k in DEFAULT_SETTINGS:
            s[k] = type(DEFAULT_SETTINGS[k])(v)
    SETTINGS_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    return s


def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


DEMO_MODE = env("DEMO_MODE", "0") == "1"
DEMO_CACHE_DIR = Path(env("DEMO_CACHE_DIR", str(Path.home() / ".cache" / "odoo-kpi")))
CACHE_TTL_SECONDS = int(env("CACHE_TTL_SECONDS", "900"))
