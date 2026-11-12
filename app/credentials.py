"""Conexión con Odoo y con el proveedor de IA.

- Datos NO secretos (URL, base de datos, usuario, proveedor, modelo) → DATA_DIR/connection.json
- Claves (API key de Odoo y del proveedor de IA) → Llavero de macOS (keyring)

En desarrollo, sin llavero disponible, las claves pueden venir del .env
(ODOO_API_KEY / AI_API_KEY); nunca se escriben en disco en texto plano.
"""

import json
import logging

import keyring
from keyring.errors import KeyringError, NoKeyringError

from . import config

log = logging.getLogger(__name__)
SERVICE = config.APP_NAME
CONN_FILE = config.DATA_DIR / "connection.json"

PROVIDERS = {
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "kind": "openai"},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "kind": "openai"},
    "anthropic": {"name": "Anthropic", "base_url": None, "kind": "anthropic"},
    "custom": {"name": "Compatible con OpenAI", "base_url": None, "kind": "openai"},
}
DEFAULT_PROVIDER = "openrouter"
DEFAULT_MODEL = {"openrouter": "deepseek/deepseek-v4-flash"}


# ----------------------------------------------------------------------------- secretos
def _secret_get(name: str):
    try:
        v = keyring.get_password(SERVICE, name)
        if v:
            return v
    except (NoKeyringError, KeyringError) as e:
        log.debug("Llavero no disponible (%s)", e)
    return config.env({"odoo_key": "ODOO_API_KEY", "ai_key": "AI_API_KEY"}.get(name, ""))


def _secret_set(name: str, value: str):
    try:
        keyring.set_password(SERVICE, name, value)
    except (NoKeyringError, KeyringError) as e:
        raise RuntimeError("No se pudo guardar la clave en el Llavero del sistema: " + str(e)) from e


# ----------------------------------------------------------------------------- conexión
def _load() -> dict:
    d = {}
    if CONN_FILE.exists():
        try:
            d = json.loads(CONN_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            d = {}
    # Valores de desarrollo desde el .env si aún no hay configuración guardada
    d.setdefault("odoo_url", config.env("ODOO_URL", ""))
    d.setdefault("odoo_db", config.env("ODOO_DB", ""))
    d.setdefault("odoo_user", config.env("ODOO_USER", ""))
    d.setdefault("ai_provider", config.env("AI_PROVIDER", DEFAULT_PROVIDER))
    d.setdefault("ai_model", config.env("AI_MODEL", DEFAULT_MODEL.get(d["ai_provider"], "")))
    d.setdefault("ai_base_url", config.env("AI_BASE_URL", ""))
    return d


def get() -> dict:
    """Conexión completa, claves incluidas (solo para uso interno del backend)."""
    d = _load()
    d["odoo_key"] = _secret_get("odoo_key") or ""
    d["ai_key"] = _secret_get("ai_key") or ""
    return d


def public() -> dict:
    """Lo que puede ver la interfaz: nunca las claves, solo si existen."""
    d = get()
    return {
        "odoo": {"url": d["odoo_url"], "db": d["odoo_db"], "user": d["odoo_user"], "has_key": bool(d["odoo_key"])},
        "ai": {"provider": d["ai_provider"], "provider_name": PROVIDERS.get(d["ai_provider"], {}).get("name", ""),
               "model": d["ai_model"], "base_url": d["ai_base_url"], "has_key": bool(d["ai_key"])},
        "configured": odoo_configured(d) and ai_configured(d),
    }


def odoo_configured(d=None) -> bool:
    d = d or get()
    return bool(d["odoo_url"] and d["odoo_user"] and d["odoo_key"])


def ai_configured(d=None) -> bool:
    d = d or get()
    needs_key = d["ai_provider"] != "custom"
    return bool(d["ai_provider"] in PROVIDERS and d["ai_model"] and (d["ai_key"] or not needs_key))


def base_url_for(d) -> str | None:
    return d.get("ai_base_url") or PROVIDERS.get(d["ai_provider"], {}).get("base_url")


def save(odoo: dict | None = None, ai: dict | None = None):
    """Guarda la conexión. Una clave vacía significa «conservar la guardada»."""
    d = _load()
    if odoo is not None:
        d["odoo_url"] = (odoo.get("url") or "").strip().rstrip("/")
        d["odoo_db"] = (odoo.get("db") or "").strip()
        d["odoo_user"] = (odoo.get("user") or "").strip()
        if odoo.get("key"):
            _secret_set("odoo_key", odoo["key"].strip())
    if ai is not None:
        provider = ai.get("provider")
        if provider not in PROVIDERS:
            raise ValueError("Proveedor de IA no válido")
        d["ai_provider"] = provider
        d["ai_model"] = (ai.get("model") or "").strip()
        d["ai_base_url"] = (ai.get("base_url") or "").strip().rstrip("/") if provider == "custom" else ""
        if ai.get("key"):
            _secret_set("ai_key", ai["key"].strip())
    CONN_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        CONN_FILE.chmod(0o600)
    except OSError:
        pass
