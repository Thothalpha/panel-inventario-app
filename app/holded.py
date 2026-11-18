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


# ----------------------------------------------------------------------------- clasificación
# Holded no tiene categorías de producto en la API: el tipo se deduce del nombre, el SKU y la descripción.
# El orden importa (p. ej. «Short … malla» es un short; «Essential … Tank» es de tirantes).
TYPE_RULES = [
    (r"calcet", "Calcetines"),
    (r"\bgorra\b|\bcap\b|_cap\b|^cap_", "Gorras"),
    (r"\bgafas\b|glasses", "Gafas"),
    (r"flask|bid[oó]n|botella", "Hidratación"),
    (r"cintur[oó]n|\bbelt\b", "Cinturones"),
    (r"short|aerosplit", "Shorts"),
    (r"tights|\bmallas?\b|legging", "Mallas"),
    (r"pantal[oó]n|\bpants?\b|_pant", "Pantalones"),
    (r"wind ?jacket|windbreaker|cortavientos|contavientos|\bwind\b|_wind|loveshell|jacket|chaqueta", "Chaquetas y cortavientos"),
    (r"long[ _]sleeve|\blong\b|_long", "Manga larga"),
    (r"tank|tirantes", "Tirantes"),
    (r"\btop\b|_top|crop", "Tops"),
    (r"merino|t-?shirt|camiseta|_cam\b|tecnic|oversi[sz]e|\bbasic\b|essential", "Camisetas"),
]
OTHER_TYPE = "Otras prendas"
COLLECTION_ATTR = "COLECCIONES"
SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "Única"]


def detect_type(*texts) -> str:
    t = " ".join(str(x or "") for x in texts).lower()
    t = re.sub(r"<[^>]+>", " ", t)
    for pattern, label in TYPE_RULES:
        if re.search(pattern, t):
            return label
    return OTHER_TYPE


def detect_collection(product: dict, known: set) -> str:
    for a in product.get("attributes") or []:
        if str(a.get("name") or "").upper() == COLLECTION_ATTR and a.get("value"):
            return str(a["value"]).strip().upper()
    name = f" {product.get('name') or ''} {product.get('sku') or ''} ".upper().replace("_", " ")
    return next((k for k in sorted(known) if f" {k} " in name), "Sin colección")


def norm_size(v) -> str:
    s = str(v or "").replace("\xa0", " ").strip()
    if not s:
        return ""
    if re.search(r"[uú]nica", s, re.I):
        return "Única"
    s = re.sub(r"(?i)^talla\s*", "", s)
    s = re.sub(r"^\d+\s*-\s*", "", s).strip()
    return s.upper() if s.upper() in SIZE_ORDER else s


def size_rank(size: str) -> int:
    return SIZE_ORDER.index(size) if size in SIZE_ORDER else len(SIZE_ORDER)


def variant_size(v: dict, idx: int) -> str:
    opts = v.get("options") or []
    size = next((o.get("value") for o in opts if "talla" in str(o.get("name") or "").lower() or "size" in str(o.get("name") or "").lower()), None)
    size = size or (opts[0].get("value") if opts else None)
    return norm_size(size) or f"Variante {idx + 1}"


def channel_of(tags) -> str:
    """Canal de venta a partir de las etiquetas del albarán (tienda, decathlon, club…)."""
    for t in tags or []:
        name = _tag_name(t).strip().lower().replace("_", " ")
        if re.fullmatch(r"[a-záéíóúñü ]{2,30}", name):  # descarta identificadores con números
            return name.capitalize()
    return "Sin canal"


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
    variants = {}            # pid → [{vid, size, sku, on_hand}]
    vid_owner = {}           # id de variante → pid
    known_collections = {str(a.get("value")).strip().upper() for p in raw for a in p.get("attributes") or []
                         if str(a.get("name") or "").upper() == COLLECTION_ATTR and a.get("value")}
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
        vlist = [v for v in (p.get("variants") or []) if not v.get("archived")]
        vcosts = [num(v.get("cost")) or num(v.get("purchase_price")) for v in vlist]
        vcosts = [x for x in vcosts if x > 0]
        cost = num(p.get("cost")) or num(p.get("purchase_price")) or (sum(vcosts) / len(vcosts) if vcosts else 0.0)
        products[pid] = {
            "id": pid, "tmpl_id": pid, "holded_id": p["id"], "ref": p.get("sku") or "", "name": p.get("name") or "",
            # Etiqueta de Holded si existe; si no, el tipo deducido del nombre
            "categ": tags[0] if tags else detect_type(p.get("name"), p.get("sku"), p.get("description")),
            "family": detect_collection(p, known_collections), "uom": "Unidades",
            "on_hand": on_hand, "free": on_hand, "virtual": on_hand, "incoming": 0.0, "outgoing": 0.0,
            "cost": cost, "price": num(p.get("price")),
            "sale_ok": bool(p.get("for_sale", True)), "purchase_ok": bool(p.get("for_purchase", True)),
        }
        if vlist:
            variants[pid] = []
            for i, v in enumerate(vlist):
                variants[pid].append({"vid": v.get("id"), "size": variant_size(v, i), "sku": v.get("sku") or "",
                                      "on_hand": num(v.get("stock"))})
                vid_owner[v.get("id")] = pid
            variants[pid].sort(key=lambda x: (size_rank(x["size"]), x["size"]))
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

    # Demanda: líneas de albaranes (salida física de mercancía). Las unidades negativas son devoluciones y se restan.
    weekly = {pid: [0.0] * len(grid) for pid in products}
    vweekly = defaultdict(lambda: [0.0] * len(grid))          # (pid, vid) → demanda semanal de la talla
    channels = defaultdict(lambda: {"units": 0.0, "revenue": 0.0, "docs": 0})
    channel_by_product = defaultdict(lambda: defaultdict(float))
    returns_52w, revenue_52w = defaultdict(float), defaultdict(float)
    year_ago = today.toordinal() - 364
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
        recent = bool(d and d.toordinal() >= year_ago)
        ch = channel_of(w.get("tags"))
        if recent:
            channels[ch]["docs"] += 1
        for ln in w.get("lines") or []:
            pid = hid_to_pid.get(ln.get("product_id"))
            if pid not in products:
                continue
            units = num(ln.get("units"))
            if i is not None:
                weekly[pid][i] += units
                vid = ln.get("variant_id")
                if vid and vid_owner.get(vid) == pid:
                    vweekly[(pid, vid)][i] += units
            if recent:
                disc = num(ln.get("discount"))
                rev = num(ln.get("price")) * units * (1 - disc / 100 if 0 <= disc <= 100 else 1)
                channels[ch]["units"] += units
                channels[ch]["revenue"] += rev
                channel_by_product[pid][ch] += units
                revenue_52w[pid] += rev
                if units < 0:
                    returns_52w[pid] += -units
            if d and units > 0 and (pid not in last_move or d.isoformat() > last_move[pid]):
                last_move[pid] = d.isoformat()
    # Una semana con más devoluciones que ventas no es demanda negativa
    for series in list(weekly.values()) + list(vweekly.values()):
        for k, v in enumerate(series):
            if v < 0:
                series[k] = 0.0
    for pid, vl in variants.items():
        for v in vl:
            v["weekly"] = vweekly.get((pid, v["vid"]), [0.0] * len(grid))

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

    has_costs = sum(1 for p in products.values() if p["cost"] > 0) >= max(1, len(products) // 2)
    if not has_costs:
        warnings.append("Holded no tiene el coste de la mayoría de productos: el valor del stock, el exceso en € y el "
                        "dinero parado saldrán a cero o incompletos hasta que se carguen los costes (en el producto o en "
                        "cada variante). Mientras tanto, la clasificación ABC se hace por facturación.")

    log.info("Holded: %s productos, %s albaranes, %s llamadas a la API", len(products), len(waybills), c.calls)
    return {
        "source": "holded", "generated_at": datetime.now().isoformat(timespec="seconds"), "warnings": warnings,
        "week_starts": [d.isoformat() for d in grid], "products": products, "weekly_demand": weekly,
        "last_move": last_move, "last_in": last_in, "quants": quants, "orderpoints": orderpoints,
        "suppliers": suppliers, "pending_po": pending_po, "bom_usage": dict(bom_usage),
        # Específico de Holded (la app lo usa si existe)
        "variants": variants, "has_costs": has_costs,
        "channels": {k: {"units": round(v["units"], 1), "revenue": round(v["revenue"], 2), "docs": v["docs"]}
                     for k, v in sorted(channels.items(), key=lambda kv: -kv[1]["units"])},
        "channel_by_product": {pid: dict(v) for pid, v in channel_by_product.items()},
        "returns_52w": dict(returns_52w), "revenue_52w": {k: round(v, 2) for k, v in revenue_52w.items()},
        "labels": {"family": "Colección", "families": "Colecciones", "categ": "Tipo / etiqueta"},
    }
