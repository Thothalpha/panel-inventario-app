"""Exporta una demo ESTÁTICA del panel (sin servidor) a docs/ para GitHub Pages.

Usa siempre los datos FICTICIOS de app/demo_ficticio.py. Las respuestas del asistente son
ejemplos precalculados a partir de esos datos (en la versión real las redacta Claude
consultando Odoo en el momento).

Uso:  .venv/bin/python -m scripts.exportar_estatico
"""

import json
import os
import shutil
from datetime import date, datetime

os.environ["DEMO_MODE"] = "1"
os.environ["DEMO_SOURCE"] = "ficticio"

from app import alerts, analytics, config, data  # noqa: E402

OUT = config.ROOT / "docs"
NOTE = ("\n\n---\n_Respuesta de **ejemplo precalculada** para la demo. En la versión real la redacta Claude "
        "consultando el panel y Odoo en el momento, y puedes preguntarle lo que quieras._")


def n(v, d=0):
    if v is None:
        return "—"
    s = f"{v:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def eur(v):
    return n(v) + " €"


def dmy(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else "—"


def main():
    s = config.load_settings()
    snap = data.get_snapshot(force=True)
    metrics = analytics.compute(snap, s)
    al = alerts.evaluate(metrics, snap, s)
    al = [dict(a, acked=False) for a in al]
    ov = analytics.overview(metrics, al)
    ov["pending_po_value"] = round(sum(p["qty"] * p["price"] for p in snap["pending_po"]), 2)
    rows = list(metrics.values())

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "data" / "products").mkdir(parents=True)
    shutil.copytree(config.ROOT / "static", OUT / "static", ignore=shutil.ignore_patterns("index.html"))

    def dump(rel, obj):
        (OUT / rel).write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")

    # ------------------------------------------------------------ respuestas de ejemplo
    risk = sorted([a for a in al if a["kind"] in ("rotura", "riesgo_rotura")],
                  key=lambda a: alerts.SEV_RANK[a["severity"]])
    to_order = sorted([r for r in rows if r["suggested_order"] > 0], key=lambda r: (r["coverage_days"] or 0))
    stuck = sorted([r for r in rows if (r["coverage_days"] or 0) > s["exceso_cobertura_dias"] or
                    ((r["days_idle"] or 0) > s["sin_movimiento_dias"] and r["on_hand"] > 0)], key=lambda r: -r["value"])
    by_ref = {r["ref"]: r for r in rows}

    def order_table(items):
        t = "| Ref. | Producto | Pedir | Cobertura | Plazo | Motivo |\n|---|---|---:|---:|---:|---|\n"
        for r in items:
            why = "sin stock" if r["on_hand"] <= 0 else ("rotura antes de que llegue la compra"
                                                         if r["coverage_days"] is not None and r["coverage_days"] < r["lead_time_days"]
                                                         else "por debajo del punto de pedido")
            t += (f"| {r['ref']} | {r['name']} | {n(r['suggested_order'])} {r['uom']} | {n(r['coverage_days'])} d | "
                  f"{n(r['lead_time_days'])} d | {why} |\n")
        return t

    late = [a for a in al if a["kind"] == "compra_retrasada"]
    exp = [a for a in al if a["kind"] == "caducidad"]
    anom = [a for a in al if a["kind"] == "anomalia"]
    neg = [a for a in al if a["kind"] == "negativo"]
    summary = f"""## Situación
- **{eur(ov['stock_value'])}** en stock, rotación de **{n(ov['turnover'], 1)}×** al año.
- **{ov['alerts_by_severity']['critica']} alertas críticas** y {ov['alerts_by_severity']['alta']} altas.
- **{ov['to_order_count']} productos** necesitan pedido (≈ {eur(ov['to_order_value'])} a coste).
- La previsión acierta un **{n((ov['forecast_accuracy'] or 0) * 100)} %** en bloques de 4 semanas.

## Riesgos de rotura
""" + "\n".join(f"- **{a['ref']}** {a['name']}: {a['title'].lower()}. {a['detail']}" for a in risk[:5]) + f"""

## Qué pedir
{order_table(to_order[:8])}
## Dinero parado
""" + "\n".join(f"- **{r['ref']}** {r['name']}: {eur(r['value'])}, "
                + (f"{n(r['coverage_days'])} días de cobertura" if r['coverage_days'] else f"{r['days_idle']} días sin movimiento")
                for r in stuck[:4]) + """
- Recomendación: frenar las compras de estas referencias y valorar promoción o liquidación del material de campañas antiguas.

## Señales a vigilar
""" + "\n".join([f"- **Compra retrasada**: {a['title']} ({a['ref']}). {a['detail']}" for a in late] +
                [f"- **Caducidad**: {a['title']} ({a['ref']})." for a in exp] +
                [f"- **Demanda**: {a['ref']} {a['title'].lower()}. Confirmar con Ventas si es un pedido puntual." for a in anom] +
                [f"- **Datos**: {a['ref']} con stock negativo ({a['detail']}): falta registrar una recepción o un ajuste." for a in neg]) + NOTE

    cron = [r for r in rows if r["categ"].startswith("CRONIDOL ")]
    sug_a = "## Qué pedir esta semana\n\n" + order_table(to_order) + \
            f"\nTotal aproximado: **{eur(sum(r['suggested_order'] * r['cost'] for r in to_order))}** a coste. " \
            "Las cantidades ya incluyen el stock de seguridad (nivel de servicio del 95 %), las compras en curso y el mínimo de cada proveedor." + NOTE
    soon = sorted([r for r in rows if r["daily_forecast"] > 0 and (r["coverage_days"] or 0) < 30],
                  key=lambda r: r["coverage_days"] or 0)
    sug_b = "## Sin stock en los próximos 30 días\n\n| Ref. | Producto | Stock | Rotura estimada | ¿La cubre una compra? |\n|---|---|---:|---|---|\n" + \
            "".join(f"| {r['ref']} | {r['name']} | {n(r['on_hand'])} | {dmy(r['stockout_date'])} | "
                    f"{'sí' if (r['coverage_virtual_days'] or 0) >= r['lead_time_days'] else 'no'} |\n" for r in soon) + NOTE
    sug_c = "## Dinero parado\n\n" + "\n".join(
        f"- **{r['ref']}** {r['name']}: {eur(r['value'])} · " +
        (f"{n(r['coverage_days'])} días de cobertura" if r["coverage_days"] else f"{r['days_idle']} días sin movimiento")
        for r in stuck) + "\n\n**Qué haría:** no reponer estas referencias hasta bajar de 6 meses de cobertura, revisar el lote mínimo de compra y, en el material de campaña, liquidar o reutilizar." + NOTE
    sug_d = "## Componentes de Cronidol\n\n| Ref. | Componente | Stock | Cobertura | Plazo | Estado |\n|---|---|---:|---:|---:|---|\n" + "".join(
        f"| {r['ref']} | {r['name']} | {n(r['on_hand'], 1 if r['uom'] == 'kg' else 0)} {r['uom']} | {n(r['coverage_days'])} d | {n(r['lead_time_days'])} d | "
        f"{'⚠ pedir' if r['suggested_order'] > 0 else 'ok'} |\n" for r in sorted(cron, key=lambda r: r["coverage_days"] or 0)) + \
        "\nLa cobertura de los componentes se calcula con su consumo real en fabricación, no con las ventas del producto terminado." + NOTE
    rule_box = {"name": "Cajas Cronidol por debajo de 5.000", "description": "Pedir cajas a Cartonajes (plazo 15 días)",
                "scope_type": "products", "scope_values": ["BOX-CRN-901"], "metric": "on_hand", "op": "<",
                "threshold": 5000.0, "severity": "alta", "active": True}
    box = by_ref["BOX-CRN-901"]
    sug_e = (f"Te preparo la alerta: saltará cuando el stock a mano de **BOX-CRN-901** ({box['name']}) baje de 5.000 unidades. "
             f"Hoy hay {n(box['on_hand'])}, así que de momento no salta. Pulsa **Guardar alerta** para activarla." + NOTE)

    def q(*tools):
        return [{"tool": t, "input": {}} for t in tools]

    canned = {
        "summary": {"text": summary, "proposals": [], "queries": q("resumen_inventario", "listar_alertas", "buscar_productos")},
        "chat": {
            "¿Qué tengo que pedir esta semana y cuánto?": {"text": sug_a, "proposals": [], "queries": q("buscar_productos", "listar_alertas")},
            "¿Qué productos se quedan sin stock en los próximos 30 días?": {"text": sug_b, "proposals": [], "queries": q("buscar_productos")},
            "¿Dónde tengo más dinero parado y qué hago con él?": {"text": sug_c, "proposals": [], "queries": q("buscar_productos", "listar_alertas")},
            "¿Cómo va la cobertura de los componentes de Cronidol?": {"text": sug_d, "proposals": [], "queries": q("buscar_productos", "detalle_producto")},
            "Avísame si las cajas de Cronidol bajan de 5.000 unidades": {"text": sug_e, "proposals": [rule_box], "queries": q("buscar_productos", "proponer_alerta")},
        },
        "products": {},
    }
    for r in rows:
        status = ("sin stock" if r["on_hand"] <= 0 else
                  f"cubre {n(r['coverage_days'])} días" if r["coverage_days"] is not None else "sin demanda prevista")
        own = [a for a in al if a["product_id"] == r["id"]]
        canned["products"][r["ref"]] = {"text": (
            f"## {r['ref']} · {r['name']}\n\n"
            f"- **Stock**: {n(r['on_hand'])} {r['uom']} ({status}); previsto con compras en curso: {n(r['virtual'])}.\n"
            f"- **Demanda prevista**: {n(r['forecast_30d'])} {r['uom']} en 30 días ({r['method']}"
            + (f", precisión {n((r['accuracy'] or 0) * 100)} %" if r["accuracy"] is not None else "") + ").\n"
            f"- **Plazo de entrega**: {n(r['lead_time_days'])} días · stock de seguridad {n(r['safety_stock'])} · punto de pedido {n(r['reorder_point'])}.\n"
            + ("".join(f"- **Alerta {a['severity']}**: {a['title']}. {a['action']}.\n" for a in own) if own else "- Sin alertas activas.\n")
            + (f"\n**Recomendación**: pedir {n(r['suggested_order'])} {r['uom']}" + (f" a {r['supplier']}" if r["supplier"] else "") + "."
               if r["suggested_order"] > 0 else "\n**Recomendación**: no hace falta pedir ahora.") + NOTE),
            "proposals": [], "queries": q("detalle_producto")}

    # ------------------------------------------------------------ datos
    dump("data/dashboard.json", {
        "source": "demo", "static": True, "generated_at": snap["generated_at"], "warnings": snap["warnings"],
        "ai_available": True, "overview": ov, "alerts": al,
        "products": sorted(rows, key=lambda r: -r["value"]),
        "last_summary": None,
    })
    for pid in metrics:
        dump(f"data/products/{pid}.json", analytics.product_detail(snap, s, pid, metrics))
    dump("data/rules.json", {"rules": [], "metrics": alerts.METRICS, "ops": list(alerts.OPS), "severities": alerts.SEVERITIES})
    dump("data/settings.json", {"settings": s, "defaults": config.DEFAULT_SETTINGS})
    dump("data/ai.json", canned)

    html = (config.ROOT / "static" / "index.html").read_text(encoding="utf-8")
    marker = '<script src="static/app.js"></script>'
    assert marker in html
    html = html.replace(marker, '<script>window.STATIC_DEMO = true;</script>\n' + marker)
    # Versión en las URLs de los recursos: tras cada publicación el navegador no usa copias viejas
    ver = datetime.now().strftime("%Y%m%d%H%M")
    for asset in ("static/app.js", "static/styles.css"):
        html = html.replace(f'"{asset}"', f'"{asset}?v={ver}"')
    (OUT / "index.html").write_text(html, encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    (OUT / "README.md").write_text(
        "# Panel de inventario · demo\n\n"
        "Vista previa **estática** de un panel de inventario con alertas, previsión de demanda y asistente de IA, "
        "preparada para revisar funciones y diseño.\n\n"
        "- Todos los datos son **ficticios** (productos, cantidades, costes, proveedores y pedidos inventados).\n"
        "- El asistente de IA muestra **respuestas de ejemplo**; en la versión real consulta los datos en el momento.\n"
        "- Lo que se guarda en la demo (alertas personalizadas, alertas vistas) solo queda en el navegador de quien la usa.\n",
        encoding="utf-8")
    print(f"Demo estática en {OUT} · {len(rows)} productos · {len(al)} alertas · {date.today().isoformat()}")


if __name__ == "__main__":
    main()
