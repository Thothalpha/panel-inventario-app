"""Previsión de demanda y métricas de inventario por producto.

Métodos (elegidos automáticamente por producto según el patrón de demanda):
  - Holt amortiguado (nivel + tendencia amortiguada) para demanda regular, con
    estacionalidad multiplicativa por bloques de 4 semanas si hay >= 78 semanas de
    historia y mejora el backtest.
  - Croston-SBA para demanda intermitente (intervalo medio entre demandas > 1,32 semanas),
    típica de componentes y clientes B2B con pedidos grandes y espaciados.
La precisión se mide con backtest: se ajusta sin las últimas 12 semanas y se compara
(precisión = 1 - WAPE).
"""

import math
import statistics
from .alerts import family as _family
from datetime import date, timedelta

Z_TABLE = {0.80: 0.842, 0.85: 1.036, 0.90: 1.282, 0.95: 1.645, 0.975: 1.960, 0.98: 2.054, 0.99: 2.326}


def z_for(service: float) -> float:
    return Z_TABLE.get(round(service, 3)) or min(Z_TABLE.items(), key=lambda kv: abs(kv[0] - service))[1]


# ----------------------------------------------------------------------------- modelos
def _holt(y, alpha, beta, phi):
    level, trend = y[0], (y[1] - y[0]) if len(y) > 1 else 0.0
    errs = []
    for t in range(1, len(y)):
        f = level + phi * trend
        errs.append(y[t] - f)
        new_level = alpha * y[t] + (1 - alpha) * (level + phi * trend)
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        level = new_level
    return level, trend, errs


def _holt_forecast(level, trend, phi, h):
    out, damp = [], 0.0
    for k in range(1, h + 1):
        damp += phi ** k
        out.append(max(0.0, level + damp * trend))
    return out


def fit_holt(y, h):
    best = None
    for a in (0.1, 0.2, 0.3, 0.5):
        for b in (0.05, 0.1, 0.2):
            level, trend, errs = _holt(y, a, b, 0.9)
            sse = sum(e * e for e in errs[2:]) if len(errs) > 2 else sum(e * e for e in errs)
            if best is None or sse < best[0]:
                best = (sse, level, trend, errs, a, b)
    _, level, trend, errs, a, b = best
    sigma = statistics.pstdev(errs) if len(errs) > 1 else 0.0
    return _holt_forecast(level, trend, 0.9, h), sigma, {"alpha": a, "beta": b, "phi": 0.9}


def fit_croston_sba(y, h, alpha=0.1):
    z = p = None
    q = 1
    for v in y:
        if v > 0:
            if z is None:
                z, p = v, q
            else:
                z = alpha * v + (1 - alpha) * z
                p = alpha * q + (1 - alpha) * p
            q = 1
        else:
            q += 1
    if z is None:
        return [0.0] * h, 0.0, {}
    rate = (1 - alpha / 2) * z / p
    sigma = statistics.pstdev(y) if len(y) > 1 else 0.0
    return [rate] * h, sigma, {"alpha": alpha}


def seasonal_indices(y, week_starts):
    """Índice multiplicativo por bloque de 4 semanas del año (13 bloques)."""
    buckets = [[] for _ in range(13)]
    mean = sum(y) / len(y) if y else 0
    if mean <= 0:
        return None
    for v, ws in zip(y, week_starts):
        b = min(12, (date.fromisoformat(ws).isocalendar()[1] - 1) // 4)
        buckets[b].append(v / mean)
    idx = [statistics.mean(b) if b else 1.0 for b in buckets]
    m = statistics.mean(idx)
    return [max(0.2, i / m) for i in idx] if m > 0 else None


def _bucket(ws_iso):
    return min(12, (date.fromisoformat(ws_iso).isocalendar()[1] - 1) // 4)


def forecast_series(y, week_starts, h):
    """Devuelve dict con la previsión semanal (h semanas), banda, método y precisión."""
    # Recortar ceros iniciales (producto creado después del inicio de la ventana)
    first = next((i for i, v in enumerate(y) if v > 0), None)
    future_ws = [(date.fromisoformat(week_starts[-1]) + timedelta(weeks=k)).isoformat() for k in range(1, h + 1)]
    if first is None:
        return {"method": "sin demanda", "forecast": [0.0] * h, "lo": [0.0] * h, "hi": [0.0] * h,
                "accuracy": None, "future_weeks": future_ws, "sigma": 0.0, "history_weeks": 0}
    if sum(y[-26:]) == 0:
        return {"method": "sin demanda reciente (26 sem.)", "forecast": [0.0] * h, "lo": [0.0] * h,
                "hi": [0.0] * h, "accuracy": None, "future_weeks": future_ws, "sigma": 0.0,
                "history_weeks": len(y) - first}
    y = y[first:]
    ws = week_starts[first:]
    n = len(y)
    nonzero = sum(1 for v in y if v > 0)
    adi = n / nonzero if nonzero else float("inf")
    intermittent = adi > 1.32 or n < 8

    def run(series, series_ws, horizon, cand):
        if cand == "croston":
            f, _, params = fit_croston_sba(series, horizon)
            return f, "Croston-SBA (demanda intermitente)", params
        if cand == "ma13":
            k = series[-13:]
            return [sum(k) / len(k)] * horizon, "Media móvil 13 semanas", {}
        if cand == "ses":
            level = series[0]
            for v in series[1:]:
                level = 0.2 * v + 0.8 * level
            return [level] * horizon, "Suavizado exponencial simple", {"alpha": 0.2}
        if cand == "holt_season":
            idx = seasonal_indices(series, series_ws)
            if idx:
                des = [v / idx[_bucket(w)] for v, w in zip(series, series_ws)]
                f, _, params = fit_holt(des, horizon)
                last = date.fromisoformat(series_ws[-1])
                fut = [(last + timedelta(weeks=k)).isoformat() for k in range(1, horizon + 1)]
                return [v * idx[_bucket(w)] for v, w in zip(f, fut)], "Holt amortiguado + estacionalidad", params
        f, _, params = fit_holt(series, horizon)
        return f, "Holt amortiguado", params

    candidates = ["croston", "ma13"] if intermittent else ["holt", "ses", "ma13"]
    if not intermittent and n >= 78:
        candidates.append("holt_season")

    # Backtest: se reservan las 12 últimas semanas y gana el método con menor WAPE
    holdout = 12
    accuracy, best = None, "ma13"  # sin historia para backtest: lo más prudente
    if n >= holdout + 16:
        train, test = y[:-holdout], y[-holdout:]
        tws = ws[:-holdout]

        def wape(pred):
            # WAPE en bloques de 4 semanas: el horizonte real de una decisión de compra
            a = [sum(test[i:i + 4]) for i in range(0, holdout, 4)]
            b = [sum(pred[i:i + 4]) for i in range(0, holdout, 4)]
            s_ = sum(a)
            return sum(abs(x - z) for x, z in zip(a, b)) / s_ if s_ > 0 else None

        scores = {c: wape(run(train, tws, holdout, c)[0]) for c in candidates}
        valid = {c: w for c, w in scores.items() if w is not None}
        if valid:
            best = min(valid, key=valid.get)
            accuracy = max(0.0, 1 - valid[best])

    f, method, params = run(y, ws, h, best)
    # Dispersión robusta (MAD) de la demanda semanal reciente alrededor de la previsión:
    # un pedido excepcional no dispara el stock de seguridad
    recent = y[-52:]
    level = f[0] if f else 0.0
    dev = sorted(abs(v - level) for v in recent)
    mad = dev[len(dev) // 2] if dev else 0.0
    sigma = 1.4826 * mad
    z = 1.282  # banda del 80 %
    lo = [max(0.0, v - z * sigma) for v in f]
    hi = [v + z * sigma for v in f]
    return {"method": method, "forecast": f, "lo": lo, "hi": hi, "accuracy": accuracy, "params": params,
            "future_weeks": future_ws, "sigma": sigma, "history_weeks": n, "adi": round(adi, 2)}


# ----------------------------------------------------------------------------- métricas
def _round_up(q, multiple, min_qty):
    if q <= 0:
        return 0.0
    q = max(q, min_qty or 0)
    if multiple and multiple > 0:
        q = math.ceil(q / multiple) * multiple
    return float(math.ceil(q))


def compute(snapshot: dict, settings: dict) -> dict:
    ws = snapshot["week_starts"]
    h = settings["horizonte_semanas"]
    z = z_for(settings["nivel_servicio"])
    today = date.today()
    pending_by = {}
    for po in snapshot["pending_po"]:
        pending_by.setdefault(po["pid"], []).append(po)
    neg_quants = {q["pid"] for q in snapshot["quants"] if q["qty"] < 0}

    rows = {}
    for pid, p in snapshot["products"].items():
        y = snapshot["weekly_demand"].get(pid) or [0.0] * len(ws)
        fc = forecast_series(y, ws, h)
        weekly_fc = sum(fc["forecast"][:4]) / 4 if fc["forecast"] else 0.0
        daily = weekly_fc / 7
        last4, last13, last52 = sum(y[-4:]), sum(y[-13:]), sum(y[-52:])
        sup = snapshot["suppliers"].get(pid, {})
        lead = sup.get("delay") or settings["plazo_entrega_defecto_dias"]
        sigma_w = fc["sigma"]
        safety = z * sigma_w * math.sqrt(lead / 7) if daily > 0 else 0.0
        rop = daily * lead + safety
        ops = snapshot["orderpoints"].get(pid, [])
        op_min = sum(o["min"] for o in ops) if ops else None
        op_max = sum(o["max"] for o in ops) if ops else None
        multiple = max((o["multiple"] for o in ops), default=0)
        target = rop + daily * settings["periodo_revision_dias"]
        suggested = _round_up(target - p["virtual"], multiple, sup.get("min_qty", 0)) if p["virtual"] < rop else 0.0
        stock_base = max(p["on_hand"], 0.0)
        coverage = stock_base / daily if daily > 0 else (None if stock_base > 0 else 0.0)
        coverage_virtual = max(p["virtual"], 0) / daily if daily > 0 else None
        stockout = (today + timedelta(days=int(coverage))).isoformat() if coverage is not None and daily > 0 else None
        last_move = snapshot["last_move"].get(pid)
        days_idle = (today - date.fromisoformat(last_move)).days if last_move else None
        # Anomalía: últimas 4 semanas vs. nivel esperado de las 26 anteriores
        base = y[-30:-4]
        anomaly_z = None
        if len(base) >= 12 and statistics.pstdev(base) > 0:
            anomaly_z = (statistics.mean(y[-4:]) - statistics.mean(base)) / statistics.pstdev(base)
        nz = [v for v in y[-52:]]
        cv = (statistics.pstdev(nz) / statistics.mean(nz)) if nz and statistics.mean(nz) > 0 else None
        pend = pending_by.get(pid, [])
        rows[pid] = {
            **{k: p[k] for k in ("id", "ref", "name", "categ", "uom", "on_hand", "free", "virtual", "incoming",
                                 "outgoing", "cost", "price")},
            "family": _family(p["ref"]), "value": round(stock_base * p["cost"], 2),
            "demand_4w": last4, "demand_13w": last13, "demand_52w": last52,
            "daily_forecast": round(daily, 3), "forecast_30d": round(daily * 30, 1),
            "forecast_90d": round(sum(fc["forecast"][:13]) if h >= 13 else daily * 90, 1),
            "coverage_days": round(coverage, 1) if coverage is not None else None,
            "coverage_virtual_days": round(coverage_virtual, 1) if coverage_virtual is not None else None,
            "stockout_date": stockout, "lead_time_days": lead, "supplier": sup.get("partner"),
            "safety_stock": round(safety, 1), "reorder_point": round(rop, 1),
            "suggested_order": suggested, "orderpoint_min": op_min, "orderpoint_max": op_max,
            "last_move": last_move, "days_idle": days_idle, "last_in": snapshot["last_in"].get(pid),
            "anomaly_z": round(anomaly_z, 2) if anomaly_z is not None else None,
            "cv": round(cv, 2) if cv is not None else None,
            "method": fc["method"], "accuracy": round(fc["accuracy"], 3) if fc["accuracy"] is not None else None,
            "pending_po_qty": sum(x["qty"] for x in pend),
            "late_po": [x for x in pend if x["date"] and x["date"] < today.isoformat()],
            "negative_quant": pid in neg_quants,
            "used_in": snapshot["bom_usage"].get(pid, []),
            "consumption_value_52w": round(last52 * p["cost"], 2),
        }

    _classify(rows, settings)
    return rows


def _classify(rows, s):
    items = sorted(rows.values(), key=lambda r: r["consumption_value_52w"], reverse=True)
    total = sum(r["consumption_value_52w"] for r in items)
    acc = 0.0
    for r in items:
        if total <= 0 or r["consumption_value_52w"] <= 0:
            r["abc"] = "C"
        else:
            acc += r["consumption_value_52w"]
            share = acc / total
            r["abc"] = "A" if share <= s["abc_a"] or acc == r["consumption_value_52w"] else ("B" if share <= s["abc_b"] else "C")
        cv = r["cv"]
        r["xyz"] = None if cv is None else ("X" if cv <= s["xyz_x"] else ("Y" if cv <= s["xyz_y"] else "Z"))


def product_detail(snapshot, settings, pid, metrics):
    ws = snapshot["week_starts"]
    y = snapshot["weekly_demand"].get(pid) or [0.0] * len(ws)
    fc = forecast_series(y, ws, settings["horizonte_semanas"])
    m = metrics[pid]
    # Proyección de stock semanal: stock actual - demanda prevista + compras pendientes por fecha
    proj, stock = [], m["on_hand"]
    pend = [po for po in snapshot["pending_po"] if po["pid"] == pid]
    for i, wk in enumerate(fc["future_weeks"]):
        wk_end = (date.fromisoformat(wk) + timedelta(days=6)).isoformat()
        wk_start = wk if i else "0000-00-00"
        stock += sum(po["qty"] for po in pend if wk_start <= (po["date"] or wk) <= wk_end)
        stock -= fc["forecast"][i]
        proj.append(round(stock, 1))
    return {
        "metrics": m, "history": {"weeks": ws, "values": y}, "forecast": fc, "projection": proj,
        "quants": [q for q in snapshot["quants"] if q["pid"] == pid],
        "pending_po": pend, "orderpoints": snapshot["orderpoints"].get(pid, []),
    }


def overview(metrics: dict, alerts: list) -> dict:
    rows = list(metrics.values())
    stocked = [r for r in rows if r["on_hand"] > 0]
    value = sum(r["value"] for r in rows)
    cons = sum(r["consumption_value_52w"] for r in rows)
    sev = {"critica": 0, "alta": 0, "media": 0, "baja": 0}
    for a in alerts:
        sev[a["severity"]] = sev.get(a["severity"], 0) + 1
    dead = sum(r["value"] for r in rows if r["days_idle"] is not None and r["days_idle"] > 180 and r["on_hand"] > 0)
    over = sum(r["value"] for r in rows if r["coverage_days"] and r["coverage_days"] > 365)
    accs = [(r["accuracy"], r["consumption_value_52w"]) for r in rows if r["accuracy"] is not None]
    wacc = (sum(a * w for a, w in accs) / sum(w for _, w in accs)) if accs and sum(w for _, w in accs) > 0 else None
    by_cat = {}
    for r in rows:
        by_cat[r["categ"]] = by_cat.get(r["categ"], 0) + r["value"]
    cov_buckets = {"Sin stock": 0, "< plazo": 0, "< 30 d": 0, "30–90 d": 0, "90–180 d": 0, "180–365 d": 0, "> 365 d": 0}
    for r in rows:
        c = r["coverage_days"]
        if r["daily_forecast"] <= 0:
            continue
        if r["on_hand"] <= 0:
            cov_buckets["Sin stock"] += 1
        elif c < r["lead_time_days"]:
            cov_buckets["< plazo"] += 1
        elif c < 30:
            cov_buckets["< 30 d"] += 1
        elif c < 90:
            cov_buckets["30–90 d"] += 1
        elif c < 180:
            cov_buckets["90–180 d"] += 1
        elif c < 365:
            cov_buckets["180–365 d"] += 1
        else:
            cov_buckets["> 365 d"] += 1
    matrix = {}
    for r in rows:
        if r["xyz"]:
            k = r["abc"] + r["xyz"]
            cell = matrix.setdefault(k, {"count": 0, "value": 0.0})
            cell["count"] += 1
            cell["value"] += r["value"]
    top30 = sorted(rows, key=lambda r: r["forecast_30d"] * (r["cost"] or 1), reverse=True)[:10]
    return {
        "skus": len(rows), "skus_with_stock": len(stocked), "stock_value": round(value, 2),
        "turnover": round(cons / value, 2) if value > 0 else None,
        "dead_stock_value": round(dead, 2), "overstock_value": round(over, 2),
        "to_order_count": sum(1 for r in rows if r["suggested_order"] > 0),
        "to_order_value": round(sum(r["suggested_order"] * r["cost"] for r in rows), 2),
        "pending_po_value": None,
        "data_quality": {
            "sin_coste_con_stock": sum(1 for r in rows if r["on_hand"] > 0 and not r["cost"]),
            "sin_proveedor_con_demanda": sum(1 for r in rows if r["daily_forecast"] > 0 and not r["supplier"]),
            "stock_negativo": sum(1 for r in rows if r["on_hand"] < 0),
        }, "forecast_accuracy": round(wacc, 3) if wacc is not None else None,
        "alerts_by_severity": sev, "value_by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
        "coverage_buckets": cov_buckets, "abc_xyz": matrix,
        "top_demand_30d": [{"id": r["id"], "ref": r["ref"], "name": r["name"], "forecast_30d": r["forecast_30d"],
                            "on_hand": r["on_hand"]} for r in top30],
    }
