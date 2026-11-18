"""Extracción de datos de inventario desde Odoo (solo lectura) y modo demo desde caché local.

Todo lo que sale de aquí es un "snapshot" en memoria con TTL. Las consultas a Odoo
se hacen con OdooReadOnly, que bloquea cualquier método que no sea de lectura.
"""

import json
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import config, credentials
from .odoo_client import OdooReadOnly, detect_db

_lock = threading.Lock()
_cache = {"snapshot": None, "ts": 0.0}
_client = None

DEMAND_DEST = ["customer", "production"]


class NotConfigured(Exception):
    """Falta configurar la conexión con Odoo."""


def make_client(url, db, user, key) -> OdooReadOnly:
    db = db or detect_db(url)
    return OdooReadOnly(url=url, db=db, user=user, api_key=key)


def client() -> OdooReadOnly:
    global _client
    if _client is None:
        c = credentials.get()
        if not credentials.odoo_configured(c):
            raise NotConfigured("Configura la conexión con Odoo")
        _client = make_client(c["odoo_url"], c["odoo_db"], c["odoo_user"], c["odoo_key"])
    return _client


ACCESS_CHECKS = {
    "product.product": "Productos", "stock.quant": "Stock", "stock.move": "Movimientos de stock",
    "stock.warehouse.orderpoint": "Reglas de reabastecimiento", "purchase.order.line": "Compras",
    "mrp.production": "Fabricación", "sale.order.line": "Ventas",
}


def test_connection(url, db, user, key) -> dict:
    """Prueba de la pantalla de configuración: versión, usuario y permisos de lectura."""
    o = make_client(url, db, user, key)
    version = (o.version() or {}).get("server_version", "?")
    uid = o.uid  # lanza PermissionError si el usuario o la clave no valen
    name = o.call("res.users", "read", [uid], fields=["name"])[0]["name"]
    access = {}
    for model, label in ACCESS_CHECKS.items():
        try:
            access[label] = bool(o.call(model, "check_access_rights", "read", raise_exception=False))
        except Exception:  # módulo no instalado
            access[label] = None
    return {"version": version, "url": o.url, "db": o.db, "uid": uid, "user_name": name, "access": access}


def reset():
    """Tras cambiar la conexión: nuevo cliente y datos frescos."""
    global _client
    with _lock:
        _client = None
        _cache.update(snapshot=None, ts=0.0)


# ---------------------------------------------------------------- semanas
def week_grid(n_weeks: int, today: date | None = None):
    """Lunes de las últimas n semanas COMPLETAS (la semana en curso no cuenta)."""
    today = today or date.today()
    this_monday = today - timedelta(days=today.weekday())
    first = this_monday - timedelta(weeks=n_weeks)
    return [first + timedelta(weeks=i) for i in range(n_weeks)]


def _week_index(d: date, grid):
    if not grid:
        return None
    i = (d - grid[0]).days // 7
    return i if 0 <= i < len(grid) else None


def _to_date(v):
    if not v:
        return None
    if isinstance(v, date):
        return v if not isinstance(v, datetime) else v.date()
    return datetime.fromisoformat(str(v)[:19].replace(" ", "T")).date()


def _m2o(v):
    """(id, nombre) de un many2one de Odoo o (None, '')."""
    if isinstance(v, (list, tuple)) and v:
        return v[0], (v[1] if len(v) > 1 else "")
    return None, ""


# ---------------------------------------------------------------- Odoo
def _safe(warnings, label, fn, default):
    try:
        return fn()
    except Exception as e:  # módulo no instalado, sin permisos, etc.
        warnings.append(f"{label}: no disponible ({type(e).__name__}: {str(e)[:160]})")
        return default


def _weekly_moves(o, grid, since, src_usage, dest_usage, warnings):
    """Suma semanal de product_qty de movimientos hechos src->dest. Devuelve {pid: [n]}."""
    out = defaultdict(lambda: [0.0] * len(grid))
    domain = [
        ("state", "=", "done"),
        ("date", ">=", since.isoformat()),
        ("location_id.usage", "in", src_usage),
        ("location_dest_id.usage", "in", dest_usage),
    ]
    try:
        rows = o.read_group("stock.move", domain, ["product_qty:sum"], ["product_id", "date:week"], lazy=False)
        if rows and "__range" not in rows[0]:
            raise ValueError("read_group sin __range")
        for r in rows:
            pid, _ = _m2o(r.get("product_id"))
            rng = (r.get("__range") or {}).get("date:week") or {}
            d = _to_date(rng.get("from"))
            i = _week_index(d, grid) if d else None
            if pid and i is not None:
                out[pid][i] += float(r.get("product_qty") or 0)
        return out
    except Exception:
        # Plan B: movimientos individuales (más lento pero independiente de la versión)
        rows = o.search_read_all("stock.move", domain, ["product_id", "product_qty", "date"])
        for r in rows:
            pid, _ = _m2o(r.get("product_id"))
            i = _week_index(_to_date(r.get("date")), grid)
            if pid and i is not None:
                out[pid][i] += float(r.get("product_qty") or 0)
        return out


def fetch_from_odoo() -> dict:
    s = config.load_settings()
    o = client()
    warnings = []
    grid = week_grid(s["semanas_historia"])
    since = grid[0]

    # Productos almacenables (Odoo 18: is_storable; versiones anteriores: type='product')
    pfields = ["default_code", "name", "categ_id", "uom_id", "product_tmpl_id", "qty_available",
               "free_qty", "virtual_available", "incoming_qty", "outgoing_qty", "standard_price",
               "list_price", "sale_ok", "purchase_ok"]
    try:
        prods = o.search_read_all("product.product", [("is_storable", "=", True)], pfields)
    except Exception:
        prods = o.search_read_all("product.product", [("type", "=", "product")], pfields)

    products = {}
    for p in prods:
        cid, cname = _m2o(p.get("categ_id"))
        _, uom = _m2o(p.get("uom_id"))
        tid, _ = _m2o(p.get("product_tmpl_id"))
        products[p["id"]] = {
            "id": p["id"], "tmpl_id": tid, "ref": p.get("default_code") or "", "name": p.get("name") or "",
            "categ": cname or "Sin categoría", "uom": uom,
            "on_hand": float(p.get("qty_available") or 0), "free": float(p.get("free_qty") or 0),
            "virtual": float(p.get("virtual_available") or 0), "incoming": float(p.get("incoming_qty") or 0),
            "outgoing": float(p.get("outgoing_qty") or 0), "cost": float(p.get("standard_price") or 0),
            "price": float(p.get("list_price") or 0), "sale_ok": bool(p.get("sale_ok")),
            "purchase_ok": bool(p.get("purchase_ok")),
        }

    # Demanda = salidas a clientes + consumo en fabricación, menos devoluciones
    out_w = _weekly_moves(o, grid, since, ["internal", "transit"], DEMAND_DEST, warnings)
    ret_w = _weekly_moves(o, grid, since, DEMAND_DEST, ["internal", "transit"], warnings)
    weekly = {}
    for pid in products:
        a = out_w.get(pid, [0.0] * len(grid))
        b = ret_w.get(pid, [0.0] * len(grid))
        weekly[pid] = [max(0.0, x - y) for x, y in zip(a, b)]

    # Último movimiento y última entrada
    def _last(domain):
        res = {}
        rows = o.read_group("stock.move", domain, ["date:max"], ["product_id"], lazy=False)
        for r in rows:
            pid, _ = _m2o(r.get("product_id"))
            if pid:
                res[pid] = str(r.get("date") or "")[:10]
        return res

    last_move = _safe(warnings, "Último movimiento", lambda: _last([("state", "=", "done")]), {})
    last_in = _safe(warnings, "Última entrada", lambda: _last(
        [("state", "=", "done"), ("location_id.usage", "in", ["supplier", "production"]),
         ("location_dest_id.usage", "=", "internal")]), {})

    # Ubicaciones internas → almacén
    locs = _safe(warnings, "Ubicaciones", lambda: o.search_read_all(
        "stock.location", [("usage", "=", "internal")], ["complete_name", "warehouse_id"]), [])
    loc_info = {l["id"]: (l.get("complete_name") or "", _m2o(l.get("warehouse_id"))[1]) for l in locs}

    # Lotes con caducidad (módulo product_expiry, si está)
    lot_exp = {}
    lot_fields = _safe(warnings, "Lotes", lambda: o.fields_get("stock.lot", ("type",)), {})
    if "expiration_date" in lot_fields:
        lots = _safe(warnings, "Caducidades", lambda: o.search_read_all(
            "stock.lot", [("product_qty", ">", 0)], ["name", "expiration_date"]), [])
        lot_exp = {l["id"]: (l.get("name"), str(l.get("expiration_date") or "")[:10] or None) for l in lots}

    quants_raw = _safe(warnings, "Quants", lambda: o.search_read_all(
        "stock.quant", [("location_id.usage", "=", "internal")],
        ["product_id", "location_id", "quantity", "reserved_quantity", "lot_id"]), [])
    quants = []
    for q in quants_raw:
        pid, _ = _m2o(q.get("product_id"))
        lid, lname = _m2o(q.get("location_id"))
        lot_id, lot_name = _m2o(q.get("lot_id"))
        exp = lot_exp.get(lot_id, (lot_name, None))[1] if lot_id else None
        quants.append({
            "pid": pid, "location": loc_info.get(lid, (lname, ""))[0] or lname,
            "warehouse": loc_info.get(lid, ("", ""))[1], "qty": float(q.get("quantity") or 0),
            "reserved": float(q.get("reserved_quantity") or 0), "lot": lot_name or None, "expiration": exp,
        })

    # Reglas de reabastecimiento
    ops = _safe(warnings, "Reglas de reabastecimiento", lambda: o.search_read_all(
        "stock.warehouse.orderpoint", [],
        ["product_id", "warehouse_id", "product_min_qty", "product_max_qty", "qty_multiple", "trigger"]), [])
    orderpoints = defaultdict(list)
    for r in ops:
        pid, _ = _m2o(r.get("product_id"))
        if pid and (r.get("product_min_qty") or r.get("product_max_qty")):
            orderpoints[pid].append({
                "warehouse": _m2o(r.get("warehouse_id"))[1], "min": float(r.get("product_min_qty") or 0),
                "max": float(r.get("product_max_qty") or 0), "multiple": float(r.get("qty_multiple") or 0),
                "trigger": r.get("trigger"),
            })

    # Proveedores (plazo de entrega)
    sup = _safe(warnings, "Proveedores", lambda: o.search_read_all(
        "product.supplierinfo", [], ["product_tmpl_id", "product_id", "partner_id", "delay", "min_qty", "price", "sequence"],
        order="sequence, id"), [])
    by_tmpl = {}
    for r in sup:
        tid, _ = _m2o(r.get("product_tmpl_id"))
        pid, _ = _m2o(r.get("product_id"))
        info = {"partner": _m2o(r.get("partner_id"))[1], "delay": float(r.get("delay") or 0),
                "min_qty": float(r.get("min_qty") or 0), "price": float(r.get("price") or 0)}
        key = ("p", pid) if pid else ("t", tid)
        by_tmpl.setdefault(key, info)  # el primero por secuencia
    suppliers = {}
    for pid, p in products.items():
        info = by_tmpl.get(("p", pid)) or by_tmpl.get(("t", p["tmpl_id"]))
        if info:
            suppliers[pid] = info

    # Compras confirmadas pendientes de recibir
    pol = _safe(warnings, "Compras pendientes", lambda: o.search_read_all(
        "purchase.order.line", [("order_id.state", "=", "purchase"), ("product_id", "in", list(products))],
        ["product_id", "product_qty", "qty_received", "date_planned", "order_id", "partner_id", "price_unit"]), [])
    pending_po = []
    for r in pol:
        pend = float(r.get("product_qty") or 0) - float(r.get("qty_received") or 0)
        if pend > 0:
            pending_po.append({
                "pid": _m2o(r.get("product_id"))[0], "qty": pend, "date": str(r.get("date_planned") or "")[:10],
                "order": _m2o(r.get("order_id"))[1], "partner": _m2o(r.get("partner_id"))[1],
                "price": float(r.get("price_unit") or 0),
            })

    # Dónde se usa cada componente (listas de materiales)
    bom_usage = defaultdict(list)
    boms = _safe(warnings, "Listas de materiales", lambda: o.search_read_all(
        "mrp.bom", [("active", "=", True)], ["product_tmpl_id", "product_qty"]), [])
    bom_map = {b["id"]: (_m2o(b.get("product_tmpl_id"))[1], float(b.get("product_qty") or 1)) for b in boms}
    if bom_map:
        lines = _safe(warnings, "Componentes", lambda: o.search_read_all(
            "mrp.bom.line", [("bom_id", "in", list(bom_map))], ["bom_id", "product_id", "product_qty"]), [])
        for l in lines:
            bid, _ = _m2o(l.get("bom_id"))
            pid, _ = _m2o(l.get("product_id"))
            if pid and bid in bom_map:
                parent, pqty = bom_map[bid]
                bom_usage[pid].append({"parent": parent, "qty_per_unit": float(l.get("product_qty") or 0) / (pqty or 1)})

    return {
        "source": "odoo", "generated_at": datetime.now().isoformat(timespec="seconds"),
        "warnings": warnings, "week_starts": [d.isoformat() for d in grid],
        "products": products, "weekly_demand": weekly, "last_move": last_move, "last_in": last_in,
        "quants": quants, "orderpoints": dict(orderpoints), "suppliers": suppliers,
        "pending_po": pending_po, "bom_usage": dict(bom_usage),
    }


# ---------------------------------------------------------------- demo
def fetch_demo() -> dict:
    """Snapshot a partir de la caché de la skill odoo-kpi (stock por referencia + líneas de venta).

    Solo hay demanda de producto vendido (no consumos de fabricación ni compras pendientes).
    """
    s = config.load_settings()
    grid = week_grid(s["semanas_historia"])
    stock = json.loads((config.DEMO_CACHE_DIR / "stock.json").read_text(encoding="utf-8"))
    lines = json.loads((config.DEMO_CACHE_DIR / "lines.json").read_text(encoding="utf-8"))
    mtime = datetime.fromtimestamp((config.DEMO_CACHE_DIR / "stock.json").stat().st_mtime)

    products, ref_to_id, weekly, last_move = {}, {}, {}, {}
    cost_seen = {}

    def ensure(ref, name="", categ=""):
        if ref not in ref_to_id:
            pid = len(ref_to_id) + 1
            ref_to_id[ref] = pid
            products[pid] = {
                "id": pid, "tmpl_id": pid, "ref": ref, "name": name or ref, "categ": categ or "Sin categoría",
                "uom": "Unidades", "on_hand": float(stock.get(ref, 0)), "free": float(stock.get(ref, 0)),
                "virtual": float(stock.get(ref, 0)), "incoming": 0.0, "outgoing": 0.0, "cost": 0.0,
                "price": 0.0, "sale_ok": True, "purchase_ok": True,
            }
            weekly[pid] = [0.0] * len(grid)
        return ref_to_id[ref]

    for l in lines:
        ref = l.get("referencia_producto")
        if not ref or ref not in stock:  # solo productos con stock registrado (proxy de almacenable)
            continue
        pid = ensure(ref, l.get("producto", ""), l.get("categoria_producto", ""))
        p = products[pid]
        if p["name"] == ref and l.get("producto"):
            p["name"] = l["producto"]
        d = _to_date(l.get("fecha_pedido"))
        qty = float(l.get("cantidad") or 0)
        i = _week_index(d, grid) if d else None
        if i is not None:
            weekly[pid][i] += qty
        if d and (pid not in last_move or d.isoformat() > last_move[pid]):
            last_move[pid] = d.isoformat()
        if l.get("coste_producto"):
            cost_seen[pid] = float(l["coste_producto"])
        if l.get("precio_unitario"):
            p["price"] = float(l["precio_unitario"])
    for ref in stock:
        ensure(ref)
    for pid, c in cost_seen.items():
        products[pid]["cost"] = c

    return {
        "source": "demo", "generated_at": mtime.isoformat(timespec="seconds"),
        "warnings": [
            "MODO DEMO: datos de la caché local del " + mtime.strftime("%d/%m/%Y") +
            " (stock por referencia y líneas de venta). Sin consumos de fabricación, compras pendientes, "
            "reglas de reabastecimiento ni lotes. Conecta Odoo para datos en vivo."
        ],
        "week_starts": [d.isoformat() for d in grid], "products": products, "weekly_demand": weekly,
        "last_move": last_move, "last_in": {}, "quants": [], "orderpoints": {}, "suppliers": {},
        "pending_po": [], "bom_usage": {},
    }


# ---------------------------------------------------------------- caché
def get_snapshot(force: bool = False) -> dict:
    with _lock:
        fresh = time.time() - _cache["ts"] < config.CACHE_TTL_SECONDS
        if _cache["snapshot"] is not None and fresh and not force:
            return _cache["snapshot"]
        if config.DEMO_MODE and config.env("DEMO_SOURCE") == "ficticio":
            from .demo_ficticio import build
            snap = build()
        elif config.DEMO_MODE:
            snap = fetch_demo()
        else:
            # Sin respaldo a datos de demo: en el Mac del usuario serían datos que no son suyos
            snap = fetch_from_odoo()
        _cache.update(snapshot=snap, ts=time.time())
        return snap
