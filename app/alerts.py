"""Motor de alertas: reglas automáticas (parametrizables) + reglas personalizadas guardadas en SQLite."""

import json
import sqlite3
from datetime import date, datetime, timedelta

from . import config

DB = config.DATA_DIR / "dashboard.db"
SEVERITIES = ["critica", "alta", "media", "baja"]
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}

# Métricas que puede usar una regla personalizada (clave -> etiqueta)
METRICS = {
    "on_hand": "Stock a mano",
    "free": "Stock libre (no reservado)",
    "virtual": "Stock previsto (con entradas/salidas)",
    "coverage_days": "Cobertura (días)",
    "coverage_virtual_days": "Cobertura con previsto (días)",
    "forecast_30d": "Demanda prevista 30 días",
    "demand_4w": "Demanda últimas 4 semanas",
    "value": "Valor de stock (€)",
    "days_idle": "Días sin movimiento",
    "suggested_order": "Cantidad sugerida a pedir",
    "anomaly_z": "Anomalía de demanda (z)",
    "accuracy": "Precisión de la previsión (0–1)",
}
OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
       ">=": lambda a, b: a >= b, "==": lambda a, b: a == b}


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT,
            scope_type TEXT NOT NULL DEFAULT 'all', scope_values TEXT NOT NULL DEFAULT '[]',
            metric TEXT NOT NULL, op TEXT NOT NULL, threshold REAL NOT NULL,
            severity TEXT NOT NULL DEFAULT 'media', active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS acks (
            alert_key TEXT PRIMARY KEY, acked_at TEXT NOT NULL, acked_by TEXT, note TEXT);
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, provider TEXT, model TEXT,
            prompt_tokens INTEGER, completion_tokens INTEGER, cost REAL);
        CREATE TABLE IF NOT EXISTS summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, kind TEXT NOT NULL,
            content TEXT NOT NULL, model TEXT, source TEXT);
    """)
    return con


# ----------------------------------------------------------------------------- reglas
def list_rules():
    with db() as con:
        rows = con.execute("SELECT * FROM rules ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["scope_values"] = json.loads(d["scope_values"] or "[]")
        d["active"] = bool(d["active"])
        out.append(d)
    return out


def validate_rule(rule: dict) -> dict:
    name = (rule.get("name") or "").strip()
    if not name:
        raise ValueError("La regla necesita un nombre")
    if rule.get("metric") not in METRICS:
        raise ValueError(f"Métrica no válida: {rule.get('metric')}")
    if rule.get("op") not in OPS:
        raise ValueError(f"Operador no válido: {rule.get('op')}")
    if rule.get("severity", "media") not in SEVERITIES:
        raise ValueError("Severidad no válida")
    scope = rule.get("scope_type", "all")
    if scope not in ("all", "category", "products"):
        raise ValueError("Ámbito no válido")
    values = [str(v) for v in (rule.get("scope_values") or [])]
    if scope != "all" and not values:
        raise ValueError("Indica las categorías o referencias de producto")
    return {"name": name, "description": (rule.get("description") or "").strip(), "scope_type": scope,
            "scope_values": values, "metric": rule["metric"], "op": rule["op"],
            "threshold": float(rule["threshold"]), "severity": rule.get("severity", "media"),
            "active": bool(rule.get("active", True)), "created_by": rule.get("created_by")}


def create_rule(rule: dict) -> dict:
    r = validate_rule(rule)
    with db() as con:
        cur = con.execute(
            "INSERT INTO rules (name, description, scope_type, scope_values, metric, op, threshold, severity, active,"
            " created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (r["name"], r["description"], r["scope_type"], json.dumps(r["scope_values"], ensure_ascii=False),
             r["metric"], r["op"], r["threshold"], r["severity"], int(r["active"]), r["created_by"],
             datetime.now().isoformat(timespec="seconds")))
        r["id"] = cur.lastrowid
    return r


def update_rule_active(rule_id: int, active: bool):
    with db() as con:
        con.execute("UPDATE rules SET active=? WHERE id=?", (int(active), rule_id))


def delete_rule(rule_id: int):
    with db() as con:
        con.execute("DELETE FROM rules WHERE id=?", (rule_id,))


# ----------------------------------------------------------------------------- acks
def ack(alert_key: str, by: str | None = None, note: str | None = None):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO acks VALUES (?,?,?,?)",
                    (alert_key, datetime.now().isoformat(timespec="seconds"), by, note))


def active_acks(days: int) -> dict:
    limit = (datetime.now() - timedelta(days=days)).isoformat()
    with db() as con:
        rows = con.execute("SELECT * FROM acks WHERE acked_at >= ?", (limit,)).fetchall()
    return {r["alert_key"]: dict(r) for r in rows}


# ----------------------------------------------------------------------------- resúmenes
def save_summary(kind, content, model, source):
    with db() as con:
        con.execute("INSERT INTO summaries (created_at, kind, content, model, source) VALUES (?,?,?,?,?)",
                    (datetime.now().isoformat(timespec="seconds"), kind, content, model, source))


def record_usage(provider, model, prompt_tokens, completion_tokens, cost):
    with db() as con:
        con.execute("INSERT INTO usage (created_at, provider, model, prompt_tokens, completion_tokens, cost)"
                    " VALUES (?,?,?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), provider, model,
                                              prompt_tokens, completion_tokens, cost))


def month_usage():
    """Gasto del mes en curso (el coste solo lo informan algunos proveedores, p. ej. OpenRouter)."""
    start = date.today().replace(day=1).isoformat()
    with db() as con:
        r = con.execute("SELECT COUNT(*) n, COALESCE(SUM(cost), 0) cost, SUM(cost IS NULL) sin_coste,"
                        " COALESCE(SUM(prompt_tokens + completion_tokens), 0) tokens FROM usage WHERE created_at >= ?",
                        (start,)).fetchone()
    return {"preguntas": r["n"], "coste": round(r["cost"], 4), "sin_coste": r["sin_coste"] or 0, "tokens": r["tokens"]}


def last_summary(kind="resumen"):
    with db() as con:
        r = con.execute("SELECT * FROM summaries WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
    return dict(r) if r else None


# ----------------------------------------------------------------------------- evaluación
def _d(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}" if iso and len(iso) >= 10 else "—"


def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(v)


def evaluate(metrics: dict, snapshot: dict, s: dict) -> list:
    today = date.today()
    alerts = []

    def add(kind, sev, r, title, detail, action, impact=None, extra_key=""):
        alerts.append({
            "key": f"{kind}:{r['id']}{extra_key}", "kind": kind, "severity": sev, "product_id": r["id"],
            "ref": r["ref"], "name": r["name"], "categ": r["categ"], "title": title, "detail": detail,
            "action": action, "impact_eur": round(impact, 2) if impact else None,
        })

    for r in metrics.values():
        d = r["daily_forecast"]
        lt = r["lead_time_days"]
        cov = r["coverage_days"]
        # 1. Rotura / riesgo de rotura
        if d > 0 and r["on_hand"] <= 0:
            add("rotura", "critica", r, "Sin stock con demanda activa",
                f"Demanda prevista {_fmt(r['forecast_30d'])} {r['uom']}/30 d y stock {_fmt(r['on_hand'])}.",
                f"Pedir {_fmt(r['suggested_order'])} {r['uom']} ya" if r["suggested_order"] else "Revisar reaprovisionamiento",
                impact=r["forecast_30d"] * (r["price"] or r["cost"]))
        elif d > 0 and cov is not None and cov < lt:
            cov_v = r["coverage_virtual_days"]
            covered = cov_v is not None and cov_v >= lt
            sev = "media" if covered else ("critica" if cov < lt * 0.5 else "alta")
            add("riesgo_rotura", sev, r, f"Cobertura {_fmt(cov)} d < plazo de entrega {_fmt(lt)} d",
                f"Rotura estimada el {_d(r['stockout_date'])}. " +
                ("Las entradas previstas cubren el plazo." if covered else "Las entradas previstas NO cubren el plazo."),
                "Vigilar recepción de la compra en curso" if covered else f"Pedir {_fmt(r['suggested_order'])} {r['uom']}",
                impact=max(0, lt - cov) * d * (r["price"] or r["cost"]))
        # 2. Por debajo del mínimo de la regla de reabastecimiento de Odoo
        if r["orderpoint_min"] and r["virtual"] < r["orderpoint_min"]:
            add("bajo_minimo", "alta", r, "Previsto por debajo del mínimo de Odoo",
                f"Previsto {_fmt(r['virtual'])} < mínimo {_fmt(r['orderpoint_min'])}.",
                "Lanzar la regla de reabastecimiento o pedir manualmente")
        # 3. Sin regla y por debajo del punto de pedido calculado
        elif r["orderpoint_min"] is None and d > 0 and r["virtual"] < r["reorder_point"] and r["on_hand"] > 0 \
                and not (cov is not None and cov < lt):
            add("punto_pedido", "media", r, "Por debajo del punto de pedido calculado",
                f"Previsto {_fmt(r['virtual'])} < punto de pedido {_fmt(r['reorder_point'])} "
                f"(seguridad {_fmt(r['safety_stock'])}, plazo {_fmt(lt)} d).",
                f"Pedir {_fmt(r['suggested_order'])} {r['uom']} y crear regla mín/máx en Odoo")
        # 4. Exceso de stock
        if cov is not None and d > 0 and cov > s["exceso_cobertura_dias"] and r["value"] >= s["exceso_valor_min"]:
            excess_units = r["on_hand"] - d * s["exceso_cobertura_dias"]
            add("exceso", "media" if r["value"] > 5 * s["exceso_valor_min"] else "baja", r,
                f"Exceso: {_fmt(cov)} días de cobertura",
                f"Valor inmovilizado {_fmt(r['value'])} €.", "Frenar compras / promoción / revisar lote mínimo",
                impact=excess_units * r["cost"])
        # 5. Stock sin movimiento
        if r["on_hand"] > 0 and r["days_idle"] is not None and r["days_idle"] > s["sin_movimiento_dias"]:
            add("sin_movimiento", "media" if r["value"] >= s["exceso_valor_min"] else "baja", r,
                f"Sin movimiento hace {r['days_idle']} días", f"{_fmt(r['on_hand'])} {r['uom']} · {_fmt(r['value'])} €.",
                "Valorar liquidación, reutilización o regularización", impact=r["value"])
        # 6. Stock negativo
        if r["negative_quant"] or r["on_hand"] < 0:
            add("negativo", "alta", r, "Stock negativo", f"Stock a mano {_fmt(r['on_hand'])} {r['uom']}"
                + (" (hay ubicaciones con cantidad < 0)." if r["negative_quant"] else "."),
                "Revisar consumos/entregas sin recepción y hacer ajuste de inventario")
        # 7. Anomalía de demanda
        if r["anomaly_z"] is not None and abs(r["anomaly_z"]) >= s["anomalia_z"]:
            up = r["anomaly_z"] > 0
            add("anomalia", "media", r, f"Demanda {'disparada' if up else 'hundida'} (z={_fmt(r['anomaly_z'])})",
                f"Últimas 4 semanas: {_fmt(r['demand_4w'])} {r['uom']} frente al patrón de las 26 anteriores.",
                "Confirmar si es puntual (pedido grande) o un cambio de tendencia")
        # 8. Compras retrasadas
        for po in r["late_po"]:
            add("compra_retrasada", "alta", r, f"Compra {po['order']} retrasada",
                f"{_fmt(po['qty'])} {r['uom']} de {po['partner']} previstos el {_d(po['date'])}.",
                "Reclamar al proveedor", extra_key=f":{po['order']}")

    # 9. Caducidades
    limit = (today + timedelta(days=s["caducidad_aviso_dias"])).isoformat()
    for q in snapshot["quants"]:
        if q["expiration"] and q["qty"] > 0 and q["expiration"] <= limit and q["pid"] in metrics:
            r = metrics[q["pid"]]
            expired = q["expiration"] < today.isoformat()
            add("caducidad", "critica" if expired else "alta", r,
                f"Lote {q['lot']} {'caducado' if expired else 'caduca'} el {_d(q['expiration'])}",
                f"{_fmt(q['qty'])} {r['uom']} en {q['location']}.", "Priorizar salida (FEFO) o retirar",
                impact=q["qty"] * r["cost"], extra_key=f":{q['lot']}")

    # 10. Reglas personalizadas
    for rule in list_rules():
        if not rule["active"]:
            continue
        vals = {v.lower() for v in rule["scope_values"]}
        for r in metrics.values():
            if rule["scope_type"] == "category" and r["categ"].lower() not in vals and \
                    not any(v in r["categ"].lower() for v in vals):
                continue
            if rule["scope_type"] == "products" and r["ref"].lower() not in vals:
                continue
            v = r.get(rule["metric"])
            if v is None:
                continue
            if OPS[rule["op"]](float(v), rule["threshold"]):
                add(f"regla{rule['id']}", rule["severity"], r, rule["name"],
                    f"{METRICS[rule['metric']]} = {_fmt(float(v))} ({rule['op']} {_fmt(rule['threshold'])}).",
                    rule["description"] or "Revisar")

    acks = active_acks(s["ack_dias"])
    for a in alerts:
        a["acked"] = a["key"] in acks
    alerts.sort(key=lambda a: (a["acked"], SEV_RANK[a["severity"]], -(a["impact_eur"] or 0)))
    return alerts
