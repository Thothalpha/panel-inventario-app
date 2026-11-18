"""Conexión de cada sección con su ERP (Odoo u Holded) y con su proveedor de IA.

- Datos NO secretos (URL, base de datos, usuario, proveedor, modelo) → <sección>/connection.json
- Claves (del ERP y del proveedor de IA) → Llavero de macOS (keyring). Cada sección tiene las suyas:
  la sección Odoo conserva los nombres de la v0.2 («odoo_key», «ai_key»); Holded usa «holded:…».

En desarrollo, sin llavero disponible, las claves pueden venir del .env
(ODOO_API_KEY / HOLDED_API_KEY / AI_API_KEY); nunca se escriben en disco en texto plano.
"""

import json
import logging

import keyring
from keyring.errors import KeyringError, NoKeyringError

from . import config
from .odoo_client import normalize_url

log = logging.getLogger(__name__)
SERVICE = config.APP_NAME

PROVIDERS = {
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "kind": "openai"},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "kind": "openai"},
    "anthropic": {"name": "Anthropic", "base_url": None, "kind": "anthropic"},
    "custom": {"name": "Compatible con OpenAI", "base_url": None, "kind": "openai"},
}
DEFAULT_PROVIDER = "openrouter"
DEFAULT_MODEL = {"openrouter": "deepseek/deepseek-v4-flash"}

# Nombre del secreto en el Llavero por sección
SECRET_NAMES = {
    "odoo": {"erp_key": "odoo_key", "ai_key": "ai_key"},
    "holded": {"erp_key": "holded:erp_key", "ai_key": "holded:ai_key"},
}
ENV_FALLBACK = {"odoo_key": "ODOO_API_KEY", "holded:erp_key": "HOLDED_API_KEY", "ai_key": "AI_API_KEY",
                "holded:ai_key": "AI_API_KEY"}


def conn_file():
    return config.section_dir() / "connection.json"


# ----------------------------------------------------------------------------- secretos
def _secret_get(kind: str):
    name = SECRET_NAMES[config.section()][kind]
    try:
        v = keyring.get_password(SERVICE, name)
        if v:
            return v
    except (NoKeyringError, KeyringError) as e:
        log.debug("Llavero no disponible (%s)", e)
    return config.env(ENV_FALLBACK.get(name, ""))


def _secret_set(kind: str, value: str):
    name = SECRET_NAMES[config.section()][kind]
    try:
        keyring.set_password(SERVICE, name, value)
    except (NoKeyringError, KeyringError) as e:
        raise RuntimeError("No se pudo guardar la clave en el Llavero del sistema: " + str(e)) from e


# ----------------------------------------------------------------------------- conexión
def _load() -> dict:
    d = {}
    f = conn_file()
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            d = {}
    # Valores de desarrollo desde el .env si aún no hay configuración guardada
    if config.section() == "odoo":
        d.setdefault("odoo_url", config.env("ODOO_URL", ""))
        d.setdefault("odoo_db", config.env("ODOO_DB", ""))
        d.setdefault("odoo_user", config.env("ODOO_USER", ""))
    d.setdefault("ai_provider", config.env("AI_PROVIDER", DEFAULT_PROVIDER))
    d.setdefault("ai_model", config.env("AI_MODEL", DEFAULT_MODEL.get(d["ai_provider"], "")))
    d.setdefault("ai_base_url", config.env("AI_BASE_URL", ""))
    return d


def get() -> dict:
    """Conexión completa de la sección actual, claves incluidas (solo para uso interno del backend)."""
    d = _load()
    d["section"] = config.section()
    key = _secret_get("erp_key") or ""
    if d["section"] == "odoo":
        d["odoo_key"] = key
    else:
        d["holded_key"] = key
    d["ai_key"] = _secret_get("ai_key") or ""
    return d


def public() -> dict:
    """Lo que puede ver la interfaz: nunca las claves, solo si existen."""
    d = get()
    out = {
        "section": d["section"], "erp_name": config.erp_name(),
        "ai": {"provider": d["ai_provider"], "provider_name": PROVIDERS.get(d["ai_provider"], {}).get("name", ""),
               "model": d["ai_model"], "base_url": d["ai_base_url"], "has_key": bool(d["ai_key"])},
        "configured": erp_configured(d) and ai_configured(d),
    }
    if d["section"] == "odoo":
        out["odoo"] = {"url": d["odoo_url"], "db": d["odoo_db"], "user": d["odoo_user"], "has_key": bool(d["odoo_key"])}
    else:
        out["holded"] = {"has_key": bool(d["holded_key"]), "account": d.get("holded_account", "")}
    return out


def erp_configured(d=None) -> bool:
    d = d or get()
    if d["section"] == "odoo":
        return bool(d["odoo_url"] and d["odoo_user"] and d["odoo_key"])
    return bool(d.get("holded_key"))


odoo_configured = erp_configured  # compatibilidad


def ai_configured(d=None) -> bool:
    d = d or get()
    needs_key = d["ai_provider"] != "custom"
    return bool(d["ai_provider"] in PROVIDERS and d["ai_model"] and (d["ai_key"] or not needs_key))


def base_url_for(d) -> str | None:
    return d.get("ai_base_url") or PROVIDERS.get(d["ai_provider"], {}).get("base_url")


def save(odoo: dict | None = None, ai: dict | None = None, holded: dict | None = None):
    """Guarda la conexión de la sección actual. Una clave vacía significa «conservar la guardada»."""
    d = _load()
    sec = config.section()
    if odoo is not None and sec == "odoo":
        d["odoo_url"] = normalize_url(odoo.get("url") or "")
        d["odoo_db"] = (odoo.get("db") or "").strip()
        d["odoo_user"] = (odoo.get("user") or "").strip()
        if odoo.get("key"):
            _secret_set("erp_key", odoo["key"].strip())
    if holded is not None and sec == "holded":
        if holded.get("key"):
            _secret_set("erp_key", holded["key"].strip())
        if holded.get("account") is not None:
            d["holded_account"] = str(holded.get("account") or "")[:120]
    if ai is not None:
        provider = ai.get("provider")
        if provider not in PROVIDERS:
            raise ValueError("Proveedor de IA no válido")
        d["ai_provider"] = provider
        d["ai_model"] = (ai.get("model") or "").strip()
        d["ai_base_url"] = (ai.get("base_url") or "").strip().rstrip("/") if provider == "custom" else ""
        if ai.get("key"):
            _secret_set("ai_key", ai["key"].strip())
    f = conn_file()
    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        f.chmod(0o600)
    except OSError:
        pass
