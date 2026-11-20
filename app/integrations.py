"""Integraciones con otras apps: base para irlas añadiendo poco a poco.

Cada integración se registra con register() y aporta herramientas al asistente. El usuario la activa o
desactiva en Configuración (se guarda en el estado global de la app). Reglas para quien añada una:
  - Solo lectura por defecto. Cualquier acción que cambie datos en otra app debe devolverse como PROPUESTA
    que el usuario aplica con un botón (igual que las alertas).
  - Las claves de la integración van al Llavero (credentials), nunca a archivos ni al prompt.
  - Las herramientas devuelven datos compactos (el asistente tiene un límite de tamaño por respuesta).

Ejemplo:
    register(Integration(
        id="mi-app", name="Mi App", description="Qué aporta",
        sections=["odoo"],
        tools=[{"name": "mi_app_consulta", "description": "…", "input_schema": {...}}],
        handler=lambda tool, args, ctx: {...},
    ))
"""

from dataclasses import dataclass, field
from typing import Callable

from . import config


@dataclass
class Integration:
    id: str
    name: str
    description: str
    tools: list
    handler: Callable
    sections: list = field(default_factory=lambda: list(config.SECTIONS))
    default_enabled: bool = False


REGISTRY: dict = {}


def register(integ: Integration):
    REGISTRY[integ.id] = integ


def _enabled_ids() -> set:
    st = config.load_app_state().get("integraciones") or {}
    return {i for i, integ in REGISTRY.items() if st.get(i, integ.default_enabled)}


def active(section: str = None) -> list:
    sec = section or config.section()
    on = _enabled_ids()
    return [i for i in REGISTRY.values() if i.id in on and sec in i.sections]


def tools(section: str = None) -> list:
    return [t for i in active(section) for t in i.tools]


def handle(name: str, args: dict, ctx: dict):
    """Ejecuta la herramienta de una integración activa; None si ninguna la reconoce."""
    for i in active():
        if any(t["name"] == name for t in i.tools):
            return i.handler(name, args, ctx)
    return None


def public_list() -> list:
    on = _enabled_ids()
    return [{"id": i.id, "name": i.name, "description": i.description, "sections": i.sections,
             "enabled": i.id in on} for i in REGISTRY.values()]


def set_enabled(iid: str, enabled: bool):
    if iid not in REGISTRY:
        raise ValueError(f"No existe la integración «{iid}»")
    st = dict(config.load_app_state().get("integraciones") or {})
    st[iid] = bool(enabled)
    config.save_app_state(integraciones=st)
