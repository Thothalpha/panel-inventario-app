"""Conector de Holded (API v2, solo lectura) → mismo «snapshot» que usa la app con Odoo.

API v2: https://api.holded.com/api/v2 · Authorization: Bearer pat_<id>_<secret>
Comportamiento verificado de la v2 (especificación oficial + guía del MCP comunitario t4dhg/mcp-holded):
  - listas { items, cursor, has_more }, limit ≤ 200; listas de configuración (almacenes) sin cursor
  - importes y unidades de documentos con COMA decimal ("63500,00"); otros con punto
  - fechas ISO 8601; un token malo devuelve 403 (no 401); 100 peticiones/minuto por token

Correspondencias con el modelo de la app:
  productos (has_stock) → products · stocks[] por almacén → quants · min_stock → mínimo del ERP
  pack_items → lista de materiales · albaranes → demanda · órdenes de producción hechas → consumo
  líneas de pedidos de compra no recibidas (supplied = false) → compras pendientes
"""

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime

log = logging.getLogger(__name__)
BASE_URL = os.environ.get("HOLDED_BASE_URL") or "https://api.holded.com/api/v2"  # override solo para pruebas
MIN_INTERVAL = 0.65            # ≈ 92 peticiones/min, por debajo del límite de 100
TIMEOUT = 60
PAT_RE = re.compile(r"^pat_[A-Za-z0-9]+_[A-Za-z0-9]+$")

# Rutas que el asistente puede consultar (siempre GET)
ALLOWED_PREFIXES = ("products", "warehouses", "waybills", "purchase-orders", "purchases", "sales-orders",
                    "invoices", "production-orders", "purchase-shipments", "contacts")


class HoldedError(Exception):
    pass


# ----------------------------------------------------------------------------- utilidades
def num(v) -> float:
    """'63500,00' · '1.234,5' · '8.3' · 12 → float (0 si está vacío)."""
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def to_date(v):
    """ISO 8601, timestamp (s o ms) o DD/MM/YYYY → date."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit()):
        t = float(v)
        return datetime.fromtimestamp(t / 1000 if t > 1e11 else t).date()
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return date(int(m[3]), int(m[2]), int(m[1]))
    return None


def pid_of(holded_id: str) -> int:
    """Id numérico estable (< 2^53, apto para JavaScript) a partir del ObjectId de Holded."""
    h = re.sub(r"[^0-9a-fA-F]", "", str(holded_id or ""))[-12:] or "0"
    return int(h, 16)


def _tag_name(t):
    if isinstance(t, dict):
        return t.get("name") or t.get("label") or ""
    return str(t or "")


# ----------------------------------------------------------------------------- cliente
class HoldedReadOnly:
    def __init__(self, token: str, base_url: str | None = None):
        token = (token or "").strip()
        if not PAT_RE.match(token):
            raise ValueError("El token de Holded debe ser un token de la API v2 (empieza por «pat_»). "
                             "Créalo en Holded → Ajustes → Desarrolladores → API.")
        self.token = token
        self.base = (base_url or BASE_URL).rstrip("/")
        self._lock = threading.Lock()
        self._last = 0.0
        self.calls = 0

    def _throttle(self):
        with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def get(self, path: str, params: dict | None = None):
        path = "/" + path.lstrip("/")
        qs = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
        url = f"{self.base}{path}" + (f"?{qs}" if qs else "")
        for attempt in range(5):
            self._throttle()
            self.calls += 1
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}",
                                                       "Accept": "application/json",
                                                       "User-Agent": "PanelInventario"})
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    body = r.read().decode("utf-8")
                    if body.lstrip().startswith("<"):
                        raise HoldedError(f"Holded devolvió una página web en lugar de datos ({path})")
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    j = json.loads(e.read().decode("utf-8") or "{}")
                    detail = j.get("detail") or j.get("title") or j.get("message") or ""
                except Exception:
                    pass
                if e.code == 403:
                    raise PermissionError("Holded rechaza el token o no tiene permiso para "
                                          f"{path.split('/')[1] if '/' in path else path}"
                                          + (f" ({detail})" if detail else "")) from e
                if e.code == 429 or e.code >= 500:
                    delay = float(e.headers.get("Retry-After") or 0) or min(30, 2 ** attempt)
                    log.info("Holded %s en %s: reintento en %.0fs", e.code, path, delay)
                    time.sleep(delay)
                    continue
                if e.code == 404:
                    raise HoldedError(f"Holded no encuentra {path}" + (f": {detail}" if detail else ""))
                raise HoldedError(f"Holded respondió {e.code} en {path}" + (f": {detail}" if detail else ""))
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == 4:
                    raise OSError(f"No se pudo contactar con Holded: {e}") from e
                time.sleep(min(30, 2 ** attempt))
        raise HoldedError(f"Holded no respondió tras varios reintentos ({path})")

    def paginate(self, path: str, params: dict | None = None, max_pages: int = 500) -> list:
        items, cursor = [], None
        for _ in range(max_pages):
            p = dict(params or {}, limit=200)
            if cursor:
                p["cursor"] = cursor
            r = self.get(path, p)
            if isinstance(r, list):  # por si alguna ruta devuelve la lista directamente
                return items + r
            items.extend(r.get("items") or [])
            cursor = r.get("cursor")
            if not r.get("has_more") or not cursor:
                return items
        log.warning("Paginación de %s cortada en %s páginas", path, max_pages)
        return items


# ----------------------------------------------------------------------------- prueba de conexión
ACCESS_CHECKS = [
    ("Productos", "/products", {"limit": 1}),
    ("Almacenes", "/warehouses", {}),
    ("Albaranes", "/waybills", {"limit": 1}),
    ("Pedidos de compra", "/purchase-orders", {"limit": 1}),
    ("Órdenes de producción", "/production-orders", {"limit": 1}),
    ("Contactos", "/contacts", {"limit": 1}),
]


def test_connection(token: str, base_url: str | None = None) -> dict:
    c = HoldedReadOnly(token, base_url)
    access = {}
    for label, path, params in ACCESS_CHECKS:
        try:
            c.get(path, params)
            access[label] = True
        except PermissionError:
            access[label] = False
    if not any(access.values()):
        raise PermissionError("Holded rechaza el token: revisa que esté completo (pat_…_…), que no esté revocado "
                              "y que tenga permisos de lectura de inventario")
    account = ""
    try:
        a = c.get("/account")
        account = (a.get("name") or a.get("company_name") or a.get("company") or "") if isinstance(a, dict) else ""
    except Exception:  # el nombre de la cuenta es opcional
        pass
    return {"version": "API v2", "account": account, "access": access}


# ----------------------------------------------------------------------------- snapshot
def fetch_snapshot(c: HoldedReadOnly, grid: list, warnings: list) -> dict:
    today = date.today()
    since = grid[0]

    def week(d):
        if d is None:
            return None
        i = (d - grid[0]).days // 7
        return i if 0 <= i < len(grid) else None

    # Almacenes
    whs = {}
    default_wh = ""
    try:
        for w in c.paginate("/warehouses"):
            whs[w["id"]] = w.get("name") or "Almacén"
            if w.get("default"):
                default_wh = whs[w["id"]]
    except PermissionError as e:
        warnings.append(f"Almacenes: {e}")
    default_wh = default_wh or next(iter(whs.values()), "Almacén")

    # Productos
    raw = c.paginate("/products")
    hid_to_pid, products, quants, orderpoints, suppliers_ids = {}, {}, [], {}, {}
    pack_of = {}
    for p in raw:
        hid_to_pid[p["id"]] = pid_of(p["id"])
        if p.get("pack_items"):
            pack_of[p["id"]] = p
    for p in raw:
        if p.get("archived") or not p.get("has_stock"):
            continue
        pid = hid_to_pid[p["id"]]
        tags = [_tag_name(t) for t in (p.get("tags") or []) if _tag_name(t)]
        on_hand = num(p.get("stock"))
        products[pid] = {
            "id": pid, "tmpl_id": pid, "holded_id": p["id"], "ref": p.get("sku") or "", "name": p.get("name") or "",
            "categ": tags[0] if tags else "Sin categoría", "uom": "Unidades",
            "on_hand": on_hand, "free": on_hand, "virtual": on_hand, "incoming": 0.0, "outgoing": 0.0,
            "cost": num(p.get("cost")) or num(p.get("purchase_price")), "price": num(p.get("price")),
            "sale_ok": bool(p.get("for_sale", True)), "purchase_ok": bool(p.get("for_purchase", True)),
        }
        stocks = p.get("stocks") or []
        if stocks:
            per_wh = defaultdict(float)
            for s in stocks:
                per_wh[s.get("warehouse_id")] += num(s.get("stock"))
            for wid, q in per_wh.items():
                quants.append({"pid": pid, "location": whs.get(wid, default_wh), "warehouse": whs.get(wid, default_wh),
                               "qty": q, "reserved": 0.0, "lot": None, "expiration": None})
        elif on_hand:
            quants.append({"pid": pid, "location": default_wh, "warehouse": default_wh, "qty": on_hand,
                           "reserved": 0.0, "lot": None, "expiration": None})
        mn = num(p.get("min_stock"))
        if mn > 0:
            orderpoints[pid] = [{"warehouse": default_wh, "min": mn, "max": 0.0, "multiple": 0.0, "trigger": "holded"}]
        if p.get("supplier_id"):
            suppliers_ids[pid] = p["supplier_id"]

    # Listas de materiales: componentes de los packs
    bom_usage = defaultdict(list)
    for parent in pack_of.values():
        for it in parent.get("pack_items") or []:
            comp_hid = re.split(r"[^0-9a-fA-F]", str(it.get("product_selection") or ""))[0]
            cpid = hid_to_pid.get(comp_hid)
            if cpid in products:
                bom_usage[cpid].append({"parent": parent.get("name") or "", "qty_per_unit": num(it.get("units")) or 1.0})

    # Demanda: líneas de albaranes (salida física de mercancía)
    weekly = {pid: [0.0] * len(grid) for pid in products}
    last_move = {}
    try:
        waybills = c.paginate("/waybills", {"start_date": since.isoformat(), "sort": "date"})
    except PermissionError as e:
        warnings.append(f"Albaranes: {e}. Sin ellos no hay demanda ni previsión.")
        waybills = []
    for w in waybills:
        if w.get("status") in ("cancelled", "failed"):
            continue
        d = to_date(w.get("date"))
        i = week(d)
        for ln in w.get("lines") or []:
            pid = hid_to_pid.get(ln.get("product_id"))
            if pid not in products:
                continue
            if i is not None:
                weekly[pid][i] += max(0.0, num(ln.get("units")))
            if d and (pid not in last_move or d.isoformat() > last_move[pid]):
                last_move[pid] = d.isoformat()

    # Consumo en fabricación: órdenes de producción terminadas × componentes del producto fabricado
    try:
        for po in c.paginate("/production-orders"):
            if not po.get("is_built"):
                continue
            d = to_date(po.get("date"))
            i = week(d)
            mp = po.get("manufactured_product") or {}
            parent = pack_of.get(mp.get("product_id"))
            qty = num(mp.get("quantity"))
            if not parent or not qty:
                continue
            for it in parent.get("pack_items") or []:
                comp_hid = re.split(r"[^0-9a-fA-F]", str(it.get("product_selection") or ""))[0]
                cpid = hid_to_pid.get(comp_hid)
                if cpid in products:
                    if i is not None:
                        weekly[cpid][i] += qty * (num(it.get("units")) or 1.0)
                    if d and (cpid not in last_move or d.isoformat() > last_move[cpid]):
                        last_move[cpid] = d.isoformat()
    except PermissionError as e:
        warnings.append(f"Órdenes de producción: {e}. La demanda no incluye el consumo en fabricación.")

    # Compras: líneas no recibidas = pendientes; recibidas → última entrada
    pending_po, last_in = [], {}
    try:
        recent = date(today.year - 1, today.month, 1).isoformat()
        for po in c.paginate("/purchase-orders", {"start_date": recent, "sort": "date"}):
            if po.get("status") in ("cancelled", "failed"):
                continue
            d = to_date(po.get("date"))
            due = to_date(po.get("due_date")) or d
            for ln in po.get("lines") or []:
                pid = hid_to_pid.get(ln.get("product_id"))
                if pid not in products:
                    continue
                if ln.get("supplied"):
                    if d and (pid not in last_in or d.isoformat() > last_in[pid]):
                        last_in[pid] = d.isoformat()
                    continue
                qty = num(ln.get("units"))
                if qty > 0:
                    pending_po.append({"pid": pid, "qty": qty, "date": due.isoformat() if due else "",
                                       "order": po.get("document_number") or po.get("id", "")[:8],
                                       "partner": po.get("contact_name") or "", "price": num(ln.get("price"))})
                    products[pid]["incoming"] += qty
    except PermissionError as e:
        warnings.append(f"Pedidos de compra: {e}. Sin compras pendientes.")
    for p in products.values():
        p["virtual"] = p["on_hand"] + p["incoming"] - p["outgoing"]

    # Proveedores (nombre); Holded no guarda plazo de entrega → se usa el plazo por defecto
    suppliers, names = {}, {}
    for sid in list(dict.fromkeys(suppliers_ids.values()))[:60]:
        try:
            ct = c.get(f"/contacts/{sid}")
            names[sid] = (ct.get("name") if isinstance(ct, dict) else "") or ""
        except Exception:
            names[sid] = ""
    for pid, sid in suppliers_ids.items():
        suppliers[pid] = {"partner": names.get(sid) or None, "delay": 0, "min_qty": 0.0, "price": products[pid]["cost"]}

    log.info("Holded: %s productos, %s albaranes, %s llamadas a la API", len(products), len(waybills), c.calls)
    return {
        "source": "holded", "generated_at": datetime.now().isoformat(timespec="seconds"), "warnings": warnings,
        "week_starts": [d.isoformat() for d in grid], "products": products, "weekly_demand": weekly,
        "last_move": last_move, "last_in": last_in, "quants": quants, "orderpoints": orderpoints,
        "suppliers": suppliers, "pending_po": pending_po, "bom_usage": dict(bom_usage),
    }
