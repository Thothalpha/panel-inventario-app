"""API y servidor del panel de inventario.

En la app de escritorio el servidor escucha solo en 127.0.0.1 y exige en cada llamada a /api
el token de la sesión (PANEL_APP_TOKEN), para que ninguna web abierta en el navegador pueda
usar la API local. También se valida la cabecera Host contra DNS rebinding.
"""

import json
import logging
import secrets
import threading
import time
import urllib.request
import xmlrpc.client

import anthropic
import openai
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import ai, alerts, analytics, config, credentials, data, models_catalog
from .odoo_client import DB_HINT, fault_message, is_db_fault

log = logging.getLogger(__name__)
security = HTTPBasic(auto_error=False)
APP_TOKEN = config.env("PANEL_APP_TOKEN")  # lo define la app de escritorio en cada arranque
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


def auth(creds: HTTPBasicCredentials | None = Depends(security)):
    """Modo servidor (opcional): usuario y contraseña con DASH_USER / DASH_PASSWORD."""
    user, pwd = config.env("DASH_USER"), config.env("DASH_PASSWORD")
    if not user:
        return "local"
    if creds and secrets.compare_digest(creds.username, user) and secrets.compare_digest(creds.password, pwd or ""):
        return creds.username
    raise HTTPException(401, "Credenciales requeridas", headers={"WWW-Authenticate": "Basic"})


app = FastAPI(title="Panel de inventario", dependencies=[Depends(auth)])
app.mount("/static", StaticFiles(directory=config.ROOT / "static"), name="static")


@app.middleware("http")
async def local_guard(request: Request, call_next):
    if APP_TOKEN:
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "Host no permitido"}, status_code=403)
        if request.url.path.startswith("/api/") and \
                not secrets.compare_digest(request.headers.get("x-app-token", ""), APP_TOKEN):
            return JSONResponse({"detail": "Token de sesión no válido"}, status_code=403)
    return await call_next(request)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    """Cualquier error no previsto: al registro con la traza y un mensaje útil en la interfaz."""
    log.exception("Error no controlado en %s %s", request.method, request.url.path)
    return JSONResponse({"detail": f"Error inesperado ({type(exc).__name__}): {str(exc)[:300]}. "
                                   "Detalles en ~/Library/Logs/PanelInventario/app.log"}, status_code=500)


_mcache = {"key": None, "metrics": None}
_mlock = threading.Lock()


def state(force=False):
    s = config.load_settings()
    snap = data.get_snapshot(force=force)
    key = (snap["generated_at"], id(snap), json.dumps(s, sort_keys=True))
    with _mlock:
        if _mcache["key"] != key:
            _mcache.update(key=key, metrics=analytics.compute(snap, s))
        metrics = _mcache["metrics"]
    al = alerts.evaluate(metrics, snap, s)
    return s, snap, metrics, al


def _odoo_errors(fn, *args):
    try:
        return fn(*args)
    except data.NotConfigured as e:
        raise HTTPException(409, str(e))
    except PermissionError as e:
        raise HTTPException(401, f"Odoo: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except xmlrpc.client.Fault as f:
        log.warning("Odoo devolvió un error: %s", f.faultString)
        if is_db_fault(f):
            raise HTTPException(400, "La base de datos indicada no existe en este Odoo. " + DB_HINT)
        raise HTTPException(502, f"Odoo devolvió un error: {fault_message(f)}")
    except xmlrpc.client.ProtocolError as e:
        log.warning("Error de protocolo con Odoo: %s %s %s", e.errcode, e.errmsg, e.url)
        hint = " La dirección redirige a otra: usa la dirección final, con https://." if e.errcode in (301, 302, 303, 307, 308) \
            else " Revisa la dirección de Odoo." if e.errcode == 404 else ""
        raise HTTPException(502, f"Odoo respondió {e.errcode} {e.errmsg}.{hint}")
    except (OSError, TimeoutError) as e:
        raise HTTPException(502, f"No se pudo contactar con Odoo: {e}")


@app.get("/")
def index():
    return FileResponse(config.ROOT / "static" / "index.html")


@app.get("/api/dashboard")
def dashboard(refresh: bool = False):
    s, snap, metrics, al = _odoo_errors(state, refresh)
    ov = analytics.overview(metrics, [a for a in al if not a["acked"]])
    ov["pending_po_value"] = round(sum(p["qty"] * p["price"] for p in snap["pending_po"]), 2)
    return {
        "source": snap["source"], "generated_at": snap["generated_at"], "warnings": snap["warnings"],
        "ai_available": ai.ai_available(), "overview": ov, "alerts": al,
        "products": sorted(metrics.values(), key=lambda r: -r["value"]),
        "last_summary": alerts.last_summary(),
    }


@app.get("/api/products/{pid}")
def product(pid: int):
    s, snap, metrics, _ = _odoo_errors(state)
    if pid not in metrics:
        raise HTTPException(404, "Producto no encontrado")
    return analytics.product_detail(snap, s, pid, metrics)


@app.post("/api/alerts/ack")
async def ack(req: Request, user: str = Depends(auth)):
    body = await req.json()
    for k in body.get("keys", []):
        alerts.ack(k, by=user, note=body.get("note"))
    return {"ok": True}


@app.get("/api/rules")
def rules():
    return {"rules": alerts.list_rules(), "metrics": alerts.METRICS, "ops": list(alerts.OPS),
            "severities": alerts.SEVERITIES}


@app.post("/api/rules")
async def create_rule(req: Request, user: str = Depends(auth)):
    body = await req.json()
    body["created_by"] = user
    try:
        return alerts.create_rule(body)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e))


@app.patch("/api/rules/{rule_id}")
async def toggle_rule(rule_id: int, req: Request):
    body = await req.json()
    alerts.update_rule_active(rule_id, bool(body.get("active")))
    return {"ok": True}


@app.delete("/api/rules/{rule_id}")
def remove_rule(rule_id: int):
    alerts.delete_rule(rule_id)
    return {"ok": True}


@app.get("/api/settings")
def get_settings():
    return {"settings": config.load_settings(), "defaults": config.DEFAULT_SETTINGS}


@app.put("/api/settings")
async def put_settings(req: Request):
    try:
        return {"settings": config.save_settings(await req.json())}
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


# ----------------------------------------------------------------------------- configuración
@app.get("/api/setup")
def setup_status():
    return {**credentials.public(), "version": config.APP_VERSION, "demo": config.DEMO_MODE,
            "usage_month": alerts.month_usage()}


@app.post("/api/setup/odoo/test")
async def setup_odoo_test(req: Request):
    b = await req.json()
    stored = credentials.get()
    key = b.get("key") or stored["odoo_key"]
    if not (b.get("url") and b.get("user") and key):
        raise HTTPException(400, "Completa la dirección, el usuario y la API key")
    return await run_in_threadpool(_odoo_errors, data.test_connection, b["url"].strip().rstrip("/"),
                                   (b.get("db") or "").strip(), b["user"].strip(), key.strip())


@app.post("/api/setup/ai/test")
async def setup_ai_test(req: Request):
    b = await req.json()
    provider = b.get("provider")
    if provider not in credentials.PROVIDERS:
        raise HTTPException(400, "Proveedor no válido")
    stored = credentials.get()
    key = b.get("key") or (stored["ai_key"] if stored["ai_provider"] == provider else "")
    if not key and provider != "custom":
        raise HTTPException(400, "Introduce la clave de la API")
    if provider == "custom" and not b.get("base_url"):
        raise HTTPException(400, "Indica la URL base de la API")
    try:
        return await run_in_threadpool(models_catalog.load, provider, key, b.get("base_url"))
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except (OSError, openai.APIConnectionError, anthropic.APIConnectionError) as e:
        raise HTTPException(502, f"No se pudo contactar con el proveedor: {e}")


@app.post("/api/setup/save")
async def setup_save(req: Request):
    b = await req.json()
    try:
        credentials.save(odoo=b.get("odoo"), ai=b.get("ai"))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    data.reset()
    return credentials.public()


# ----------------------------------------------------------------------------- versiones
_vcache = {"ts": 0.0, "data": None}


def _ver(v):
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except ValueError:
        return (0,)


@app.get("/api/version")
def version_check():
    if _vcache["data"] is None or time.time() - _vcache["ts"] > 3600:
        info = {"current": config.APP_VERSION, "latest": None, "url": None, "newer": False}
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest",
                                         headers={"User-Agent": "PanelInventario", "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                rel = json.loads(r.read().decode("utf-8"))
            info.update(latest=rel.get("tag_name"), url=rel.get("html_url"), notes=rel.get("body") or "",
                        newer=_ver(rel.get("tag_name") or "0") > _ver(config.APP_VERSION))
        except Exception as e:  # sin red, sin releases o repositorio privado: no es un error para el usuario
            log.info("Comprobación de versión no disponible: %s", e)
        _vcache.update(ts=time.time(), data=info)
    return _vcache["data"]


# ----------------------------------------------------------------------------- IA
def _ai_call(fn, *args):
    try:
        return fn(*args)
    except ai.AIUnavailable as e:
        raise HTTPException(409, str(e))
    except data.NotConfigured as e:
        raise HTTPException(409, str(e))
    except (openai.AuthenticationError, anthropic.AuthenticationError):
        raise HTTPException(401, "El proveedor de IA rechaza la clave. Revísala en la configuración.")
    except (openai.RateLimitError, anthropic.RateLimitError):
        raise HTTPException(429, "Límite de uso del modelo alcanzado (frecuente en modelos gratuitos). "
                                 "Espera un poco o elige otro modelo.")
    except (openai.APIConnectionError, anthropic.APIConnectionError):
        raise HTTPException(502, "Sin conexión con el proveedor de IA")
    except (openai.APIStatusError, anthropic.APIStatusError) as e:
        if e.status_code == 402:
            raise HTTPException(402, "Sin saldo en el proveedor de IA: añade créditos en tu cuenta.")
        raise HTTPException(502, f"Error del proveedor de IA ({e.status_code}): {getattr(e, 'message', e)}")
    except PermissionError as e:
        raise HTTPException(401, f"Odoo: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.post("/api/ai/summary")
def ai_summary():
    return _ai_call(ai.summary)


@app.post("/api/ai/chat")
async def ai_chat(req: Request):
    body = await req.json()
    return await run_in_threadpool(_ai_call, ai.chat, body.get("messages") or [])
