"""Configuración: rutas, versión, variables de entorno y parámetros de negocio editables (settings.json).

Copyright (C) 2026 Thothalpha · Licencia AGPL-3.0 (ver LICENSE)
"""

import contextvars
import json
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "PanelInventario"
APP_VERSION = "0.4.1"
GITHUB_REPO = "Thothalpha/panel-inventario-app"

# Recursos (static/): dentro del paquete de PyInstaller viven en sys._MEIPASS
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
FROZEN = bool(getattr(sys, "frozen", False))

# Datos del usuario: en la app de macOS, ~/Library/Application Support/PanelInventario
DATA_DIR = Path(os.environ.get("PANEL_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------------------- secciones
# La app tiene una sección por ERP. Cada una guarda aparte su conexión, claves, ajustes, alertas y uso de IA.
SECTIONS = {"odoo": "Odoo", "holded": "Holded"}
DEFAULT_SECTION = "odoo"
_section = contextvars.ContextVar("section", default=DEFAULT_SECTION)


def section() -> str:
    return _section.get()


def set_section(name: str):
    """Fija la sección del contexto actual (cada petición HTTP lleva la suya)."""
    if name not in SECTIONS:
        raise ValueError(f"Sección no válida: {name}")
    return _section.set(name)


def erp_name(sec: str | None = None) -> str:
    return SECTIONS[sec or section()]


def section_dir(sec: str | None = None) -> Path:
    d = DATA_DIR / (sec or section())
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_file() -> Path:
    return section_dir() / "settings.json"


def _migrate_legacy():
    """Hasta la v0.2 todo era de Odoo y vivía en la raíz de DATA_DIR: se traslada a la sección odoo."""
    dest = section_dir("odoo")
    for name in ("settings.json", "dashboard.db", "connection.json"):
        src = DATA_DIR / name
        if src.exists() and not (dest / name).exists():
            shutil.move(str(src), str(dest / name))


_migrate_legacy()

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
    "talla_demanda_min_13s": 3,            # alertas por talla solo si vendió al menos N uds en 13 semanas
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
    f = settings_file()
    if f.exists():
        try:
            s.update(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return s


def save_settings(new: dict) -> dict:
    s = load_settings()
    for k, v in new.items():
        if k in DEFAULT_SETTINGS:
            s[k] = type(DEFAULT_SETTINGS[k])(v)
    settings_file().write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    return s


def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


DEMO_MODE = env("DEMO_MODE", "0") == "1"
DEMO_CACHE_DIR = Path(env("DEMO_CACHE_DIR", str(Path.home() / ".cache" / "odoo-kpi")))
CACHE_TTL_SECONDS = int(env("CACHE_TTL_SECONDS", "900"))
