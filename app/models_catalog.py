"""Catálogo de modelos de cada proveedor y validación de la clave de la API.

OpenRouter publica su lista de modelos con precio (USD por token) y parámetros admitidos;
el resto de proveedores solo devuelven identificadores.
"""

import json
import time
import urllib.error
import urllib.request

import anthropic
import openai

from . import credentials

OPENROUTER = "https://openrouter.ai/api/v1"
_cache = {"ts": 0.0, "models": None}
_NOT_CHAT = ("embedding", "tts", "whisper", "dall-e", "image", "audio", "moderation", "realtime",
             "transcribe", "search", "davinci", "babbage", "sora")


def _get_json(url, key=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "PanelInventario",
                                               **({"Authorization": f"Bearer {key}"} if key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _per_million(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f < 0 else round(f * 1_000_000, 4)


def openrouter_models(force=False):
    if _cache["models"] is not None and time.time() - _cache["ts"] < 3600 and not force:
        return _cache["models"]
    out = []
    for m in _get_json(f"{OPENROUTER}/models")["data"]:
        mid = m["id"]
        if mid.endswith(":batch"):  # procesamiento diferido: no sirve para un chat
            continue
        p = m.get("pricing") or {}
        pin, pout = _per_million(p.get("prompt")), _per_million(p.get("completion"))
        out.append({
            "id": mid, "name": m.get("name") or mid,
            "price_in": pin, "price_out": pout,
            "free": mid.endswith(":free") or (pin == 0 and pout == 0),
            "tools": "tools" in (m.get("supported_parameters") or []),
            "context": m.get("context_length"),
        })
    out.sort(key=lambda x: x["name"].lower())
    _cache.update(ts=time.time(), models=out)
    return out


def openrouter_key_info(key):
    """Valida la clave y devuelve su límite y gasto (si la cuenta los tiene)."""
    for path in ("/key", "/auth/key"):
        try:
            d = _get_json(OPENROUTER + path, key=key).get("data") or {}
            return {"label": d.get("label"), "limit": d.get("limit"), "usage": d.get("usage"),
                    "limit_remaining": d.get("limit_remaining"), "is_free_tier": d.get("is_free_tier")}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise PermissionError("OpenRouter rechaza la clave") from e
            if e.code != 404:
                raise
    raise RuntimeError("No se pudo validar la clave en OpenRouter")


def _simple(ids):
    return [{"id": i, "name": i, "price_in": None, "price_out": None, "free": False, "tools": None,
             "context": None} for i in sorted(ids)]


def load(provider, key, base_url=None) -> dict:
    """Prueba la clave y devuelve {"models": [...], "default": id, "account": {...}}."""
    if provider == "openrouter":
        account = openrouter_key_info(key)
        models = openrouter_models()
    elif provider in ("openai", "custom"):
        c = {"ai_provider": provider, "ai_base_url": base_url or ""}
        client = openai.OpenAI(api_key=key or "no-key", base_url=credentials.base_url_for(c), timeout=20)
        try:
            ids = [m.id for m in client.models.list()]
        except openai.AuthenticationError as e:
            raise PermissionError("El proveedor rechaza la clave") from e
        if provider == "openai":
            ids = [i for i in ids if not any(x in i for x in _NOT_CHAT)]
        account, models = None, _simple(ids)
    elif provider == "anthropic":
        client = anthropic.Anthropic(api_key=key, timeout=20)
        try:
            ids = [m.id for m in client.models.list()]
        except anthropic.AuthenticationError as e:
            raise PermissionError("Anthropic rechaza la clave") from e
        account, models = None, _simple(ids)
        for m in models:
            m["tools"] = True
    else:
        raise ValueError("Proveedor no válido")
    ids = {m["id"] for m in models}
    default = credentials.DEFAULT_MODEL.get(provider)
    if default not in ids:
        default = next((m["id"] for m in models if m["tools"] is not False), None)
    return {"models": models, "default": default, "account": account}
