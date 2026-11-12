"""Datos FICTICIOS con la estructura de Odoo para la demo pública (GitHub Pages).

Ninguna cifra es real: los nombres de las líneas se mantienen para que la demo resulte
familiar, pero el stock, la demanda, los costes, los precios, los proveedores y los pedidos son inventados.
Generación determinista (semilla fija) para que la demo no cambie entre exportaciones.
"""

import math
import random
from datetime import date, datetime, timedelta

from . import config
from .data import week_grid

SEED = 20261103

# (ref, nombre, categoría, coste, precio, plazo proveedor, proveedor)
FINISHED = [
    ("CRN-901", "Cronidol Crema 100 ml", "CRONIDOL", 3.10, 18.90, 28, "Laboratorio Fabricante A (ficticio)"),
    ("CRO-902", "Cronidol Parche 8x14 (5 uds)", "CRONIDOL", 2.40, 12.50, 35, "Laboratorio Fabricante A (ficticio)"),
    ("SAC-CRN-901", "Cronidol Sachet 3 ml", "CRONIDOL", 0.18, 0.00, 21, "Laboratorio Fabricante A (ficticio)"),
    ("SOS-SPR-901", "SOS Spray 50 ml", "SOS BY HAPPY PETS", 2.20, 14.90, 21, "Laboratorio Fabricante B (ficticio)"),
    ("SOS-ACE-901", "SOS Aceite 30 ml", "SOS BY HAPPY PETS", 2.80, 16.50, 21, "Laboratorio Fabricante B (ficticio)"),
    ("SOS-DOL-901", "SOS Dol 20 comprimidos", "SOS BY HAPPY PETS", 3.60, 19.90, 42, "Laboratorio Fabricante B (ficticio)"),
    ("SOS-GEL-901", "SOS Gel 100 ml", "SOS BY HAPPY PETS", 2.50, 15.90, 21, "Laboratorio Fabricante B (ficticio)"),
]
# componentes: (ref, nombre, categoría, coste, plazo, proveedor, [(padre, cantidad por unidad)])
COMPONENTS = [
    ("BOX-CRN-901", "Caja Cronidol Crema 100 ml", "CRONIDOL MC", 0.11, 15, "Cartonajes Ejemplo SL", [("CRN-901", 1)]),
    ("ENV-CRN-901", "Tubo 100 ml Cronidol", "CRONIDOL MC", 0.32, 30, "Envases Ejemplo SA", [("CRN-901", 1)]),
    ("PRO-CRN-901", "Prospecto Cronidol Crema", "CRONIDOL MC", 0.03, 10, "Imprenta Ejemplo", [("CRN-901", 1)]),
    ("BOX-CRO-902", "Caja Cronidol Parche", "CRONIDOL MC", 0.09, 15, "Cartonajes Ejemplo SL", [("CRO-902", 1)]),
    ("SAC-ENV-901", "Bobina sachet 3 ml (1.000 uds)", "CRONIDOL MC", 0.02, 25, "Envases Ejemplo SA", [("SAC-CRN-901", 1)]),
    ("MC-CRN-901", "Base crema Cronidol (kg)", "CRONIDOL MP", 9.50, 35, "Química Ejemplo SL", [("CRN-901", 0.1), ("SAC-CRN-901", 0.003)]),
    ("MC-CRN-902", "Extracto vegetal A (kg)", "CRONIDOL MP", 48.00, 45, "Extractos Ejemplo", [("CRN-901", 0.004), ("CRO-902", 0.002)]),
    ("MC-CRN-903", "Aceite esencial B (kg)", "CRONIDOL MP", 62.00, 45, "Extractos Ejemplo", [("CRN-901", 0.002)]),
    ("BOX-SOS-901", "Caja SOS Spray", "SOS MC", 0.10, 15, "Cartonajes Ejemplo SL", [("SOS-SPR-901", 1)]),
    ("ENV-SOS-901", "Frasco spray 50 ml", "SOS MC", 0.38, 30, "Envases Ejemplo SA", [("SOS-SPR-901", 1)]),
    ("ENV-SOS-902", "Frasco cuentagotas 30 ml", "SOS MC", 0.34, 30, "Envases Ejemplo SA", [("SOS-ACE-901", 1)]),
    ("BOX-SOS-DOL-901", "Caja SOS Dol", "SOS MC", 0.12, 15, "Cartonajes Ejemplo SL", [("SOS-DOL-901", 1)]),
    ("PRO-SOS-901", "Prospecto SOS (multiproducto)", "SOS MC", 0.02, 10, "Imprenta Ejemplo", [("SOS-SPR-901", 1), ("SOS-ACE-901", 1), ("SOS-GEL-901", 1)]),
    ("ENV-SOS-GEL9", "Tubo 100 ml SOS Gel", "SOS MC", 0.30, 30, "Envases Ejemplo SA", [("SOS-GEL-901", 1)]),
    ("MC-SOS-901", "Aceite vegetal C (kg)", "SOS MP", 14.00, 30, "Química Ejemplo SL", [("SOS-ACE-901", 0.03), ("SOS-SPR-901", 0.01)]),
    ("MC-SOS-902", "Principio activo D (kg)", "SOS MP", 120.00, 60, "Extractos Ejemplo", [("SOS-DOL-901", 0.006)]),
    ("MC-SOS-903", "Base gel (kg)", "SOS MP", 7.80, 30, "Química Ejemplo SL", [("SOS-GEL-901", 0.1)]),
]
# material de promoción (venta o regalo directo)
PROMO = [
    ("HP-EXP-901", "Expositor sobremesa SOS", "PROMOCIÓN", 6.50, 0, 20, "Cartelería Ejemplo"),
    ("HP-EXP-902", "Expositor suelo Cronidol (campaña 2025)", "PROMOCIÓN", 18.00, 0, 20, "Cartelería Ejemplo"),
    ("HP-FOL-901", "Folleto farmacia Cronidol", "PROMOCIÓN", 0.05, 0, 10, "Imprenta Ejemplo"),
    ("HP-MUE-901", "Muestra SOS Spray 10 ml", "PROMOCIÓN", 0.60, 0, 25, "Laboratorio Fabricante B (ficticio)"),
]


def _season(ws: date) -> float:
    # más venta en otoño-invierno (dolor articular) y algo en primavera
    w = ws.isocalendar()[1]
    return 1 + 0.25 * math.cos(2 * math.pi * (w - 2) / 52)


def build() -> dict:
    rnd = random.Random(SEED)
    s = config.load_settings()
    grid = week_grid(s["semanas_historia"])
    n = len(grid)
    today = date.today()
    products, weekly, last_move, last_in = {}, {}, {}, {}
    suppliers, orderpoints, quants, pending_po, bom_usage = {}, {}, [], [], {}
    ref_id = {}

    def add(ref, name, categ, cost, price, uom="Unidades"):
        pid = len(products) + 1
        ref_id[ref] = pid
        products[pid] = {"id": pid, "tmpl_id": pid, "ref": ref, "name": name, "categ": categ, "uom": uom,
                         "on_hand": 0.0, "free": 0.0, "virtual": 0.0, "incoming": 0.0, "outgoing": 0.0,
                         "cost": cost, "price": price, "sale_ok": price > 0, "purchase_ok": True}
        return pid

    # ---------------- producto terminado: tendencia + estacionalidad + ruido + pedidos B2B grandes
    base = {"CRN-901": 320, "CRO-902": 140, "SAC-CRN-901": 900, "SOS-SPR-901": 210, "SOS-ACE-901": 160,
            "SOS-DOL-901": 70, "SOS-GEL-901": 90}
    growth = {"CRN-901": 0.006, "CRO-902": 0.002, "SAC-CRN-901": 0.0, "SOS-SPR-901": 0.008, "SOS-ACE-901": 0.004,
              "SOS-DOL-901": 0.012, "SOS-GEL-901": -0.004}
    for ref, name, categ, cost, price, lead, sup in FINISHED:
        pid = add(ref, name, categ, cost, price)
        suppliers[pid] = {"partner": sup, "delay": lead, "min_qty": 500, "price": cost}
        series = []
        for i, ws in enumerate(grid):
            lvl = base[ref] * (1 + growth[ref]) ** (i - n) * _season(ws)
            v = rnd.gauss(lvl, lvl * 0.22)
            if ref == "SAC-CRN-901":  # muestras: salen en campañas, intermitente
                v = lvl * 4 * rnd.uniform(0.6, 1.4) if rnd.random() < 0.25 else 0
            elif rnd.random() < 0.06:  # pedido grande de distribuidor
                v += lvl * rnd.uniform(2, 4)
            series.append(max(0.0, round(v)))
        if ref == "SOS-DOL-901":  # anomalía: demanda disparada las 4 últimas semanas
            for k in range(1, 5):
                series[-k] = round(series[-k] * 2.6)
        weekly[pid] = series
        last_move[pid] = (today - timedelta(days=rnd.randint(0, 4))).isoformat()
        last_in[pid] = (today - timedelta(days=rnd.randint(10, 60))).isoformat()

    # ---------------- componentes: consumo en lotes de fabricación cada ~4 semanas
    fin_by_ref = {r[0]: ref_id[r[0]] for r in FINISHED}
    for ref, name, categ, cost, lead, sup, parents in COMPONENTS:
        uom = "kg" if "(kg)" in name else "Unidades"
        pid = add(ref, name, categ, cost, 0.0, uom)
        products[pid]["sale_ok"] = False
        suppliers[pid] = {"partner": sup, "delay": lead, "min_qty": 50 if uom == "kg" else 2000, "price": cost}
        series = [0.0] * n
        for pref, q in parents:
            pser = weekly[fin_by_ref[pref]]
            offset = rnd.randint(0, 3)
            for b in range(offset, n, 4):
                batch = sum(pser[b:b + 4]) * q
                series[b] += round(batch, 2 if uom == "kg" else 0)
        weekly[pid] = series
        bom_usage[pid] = [{"parent": next(f[1] for f in FINISHED if f[0] == pr), "qty_per_unit": q} for pr, q in parents]
        last_move[pid] = (today - timedelta(days=rnd.randint(1, 20))).isoformat()
        last_in[pid] = (today - timedelta(days=rnd.randint(20, 90))).isoformat()

    # ---------------- promoción
    for ref, name, categ, cost, price, lead, sup in PROMO:
        pid = add(ref, name, categ, cost, price)
        suppliers[pid] = {"partner": sup, "delay": lead, "min_qty": 50, "price": cost}
        series = [0.0] * n
        if ref == "HP-EXP-902":  # campaña antigua: sin movimiento desde hace ~9 meses
            for i in range(n - 60, n - 38):
                series[i] = rnd.choice([0, 0, 4, 8, 12])
            last_move[pid] = (today - timedelta(days=270)).isoformat()
        else:
            for i in range(n):
                series[i] = rnd.choice([0, 0, 10, 20, 40]) * (40 if ref == "HP-FOL-901" else 1)
            last_move[pid] = (today - timedelta(days=rnd.randint(3, 30))).isoformat()
        weekly[pid] = series

    # ---------------- stock actual: escenarios para que salgan todas las alertas
    def weekly_rate(pid):
        return sum(weekly[pid][-8:]) / 8

    cover_weeks = {  # semanas de cobertura deseadas por referencia
        "CRN-901": 1.5, "CRO-902": 9, "SAC-CRN-901": 0, "SOS-SPR-901": 6, "SOS-ACE-901": 2.2, "SOS-DOL-901": 3,
        "SOS-GEL-901": 70, "BOX-CRN-901": 30, "ENV-CRN-901": 2.5, "PRO-CRN-901": 12, "BOX-CRO-902": 18,
        "SAC-ENV-901": 6, "MC-CRN-901": 5, "MC-CRN-902": 4, "MC-CRN-903": 9, "BOX-SOS-901": 14, "ENV-SOS-901": 8,
        "ENV-SOS-902": 3, "BOX-SOS-DOL-901": 10, "PRO-SOS-901": 80, "ENV-SOS-GEL9": 7, "MC-SOS-901": 11,
        "MC-SOS-902": 5, "MC-SOS-903": 13, "HP-EXP-901": 10, "HP-EXP-902": None, "HP-FOL-901": 26, "HP-MUE-901": 6,
    }
    for ref, cw in cover_weeks.items():
        pid = ref_id[ref]
        p = products[pid]
        qty = 140.0 if cw is None else round(weekly_rate(pid) * cw, 2 if p["uom"] == "kg" else 0)
        if ref == "SAC-CRN-901":
            qty = 0.0
        p["on_hand"] = qty
    products[ref_id["MC-SOS-903"]]["on_hand"] = -12.5  # consumo registrado sin recepción → negativo
    products[ref_id["SOS-GEL-901"]]["on_hand"] = round(weekly_rate(ref_id["SOS-GEL-901"]) * 70)

    # ---------------- compras en curso
    po_n = 4100

    def po(ref, qty, days, partner=None):
        nonlocal po_n
        po_n += 1
        pid = ref_id[ref]
        pending_po.append({"pid": pid, "qty": float(qty), "date": (today + timedelta(days=days)).isoformat(),
                           "order": f"P0{po_n}", "partner": partner or suppliers[pid]["partner"],
                           "price": products[pid]["cost"]})
        products[pid]["incoming"] += qty

    po("CRN-901", 4000, 12)          # llega, pero después de la rotura
    po("SOS-ACE-901", 1500, -6)       # retrasada
    po("ENV-CRN-901", 6000, 20)
    po("MC-CRN-902", 8, 9)
    po("SOS-SPR-901", 2000, 18)

    # ---------------- reglas de reabastecimiento de Odoo (solo algunas)
    for ref, mn, mx, mult in [("CRN-901", 1500, 4500, 500), ("SOS-SPR-901", 1200, 3000, 500),
                              ("BOX-CRN-901", 5000, 15000, 1000), ("ENV-SOS-902", 1500, 4000, 500)]:
        orderpoints[ref_id[ref]] = [{"warehouse": "Almacén central", "min": mn, "max": mx, "multiple": mult, "trigger": "manual"}]

    # ---------------- previsto y quants (lotes con caducidad para el terminado)
    for pid, p in products.items():
        p["virtual"] = p["on_hand"] + p["incoming"] - p["outgoing"]
        p["free"] = p["on_hand"]
    lot_n = 2400
    for pid, p in products.items():
        if p["on_hand"] == 0:
            continue
        if p["categ"] in ("CRONIDOL", "SOS BY HAPPY PETS") and p["on_hand"] > 0:
            parts = [0.35, 0.65] if p["on_hand"] > 300 else [1.0]
            for j, frac in enumerate(parts):
                lot_n += 1
                exp_days = rnd.randint(300, 700) if j else rnd.randint(200, 500)
                if p["ref"] == "SOS-GEL-901" and j == 0:
                    exp_days = 45     # lote próximo a caducar
                if p["ref"] == "CRO-902" and j == 0:
                    exp_days = -10    # lote caducado sin retirar
                quants.append({"pid": pid, "location": "WH/Stock", "warehouse": "Almacén central",
                               "qty": round(p["on_hand"] * frac), "reserved": 0.0, "lot": f"L{lot_n}",
                               "expiration": (today + timedelta(days=exp_days)).isoformat()})
        else:
            quants.append({"pid": pid, "location": "WH/Stock" if p["on_hand"] >= 0 else "WH/Producción",
                           "warehouse": "Almacén central", "qty": p["on_hand"], "reserved": 0.0, "lot": None,
                           "expiration": None})

    return {
        "source": "demo", "generated_at": datetime.now().isoformat(timespec="seconds"),
        "warnings": ["DATOS FICTICIOS: esta demo usa productos y cifras inventados con la estructura de Odoo. "
                     "No reflejan el stock, las ventas, los costes ni los proveedores reales de Votum Labs."],
        "week_starts": [d.isoformat() for d in grid], "products": products, "weekly_demand": weekly,
        "last_move": last_move, "last_in": last_in, "quants": quants, "orderpoints": orderpoints,
        "suppliers": suppliers, "pending_po": pending_po, "bom_usage": bom_usage,
    }
