"""Asistente de IA del panel de inventario (multiproveedor).

Proveedores:
  - compatibles con la API de OpenAI (OpenRouter, OpenAI, servidores locales como Ollama/LM Studio)
  - Anthropic (Claude)

El modelo recibe herramientas de SOLO LECTURA:
  - métricas ya calculadas por el panel (rápidas, sin tocar Odoo)
  - consultas directas a Odoo (search_read / read_group) limitadas a una lista blanca de
    modelos y ejecutadas con OdooReadOnly, que rechaza cualquier método de escritura
  - proponer_alerta: valida una regla de alerta y la devuelve como PROPUESTA; solo se guarda
    si el usuario pulsa "Guardar" en el panel.
"""

import json
import logging
from datetime import date

import anthropic
import openai

from . import alerts as alerts_mod
from . import analytics, config, credentials, data

log = logging.getLogger(__name__)
MAX_TOOL_CHARS = 30000
MAX_TURNS = 14
APP_REFERER = "https://github.com/" + config.GITHUB_REPO

ODOO_MODELS = {
    "product.product", "product.template", "product.category", "product.supplierinfo", "uom.uom",
    "stock.quant", "stock.move", "stock.move.line", "stock.picking", "stock.picking.type", "stock.location",
    "stock.warehouse", "stock.warehouse.orderpoint", "stock.lot", "stock.scrap",
    "purchase.order", "purchase.order.line", "mrp.production", "mrp.bom", "mrp.bom.line",
    "sale.order", "sale.order.line",
}

SYSTEM_BASE = """Eres el analista de inventario de la empresa. Hablas con el responsable de Compras, Logística y Fabricación.

Cómo trabajas:
- Responde siempre en español, directo y con cifras concretas (unidades, días, €). Formato Markdown: titulares cortos, listas y tablas cuando haya varios productos.
- Las cifras salen de las herramientas, nunca de suposiciones. Si un dato no está, dilo y explica cómo conseguirlo.
- Empieza por las herramientas del panel (resumen_inventario, buscar_productos, detalle_producto, listar_alertas): ya traen previsión, cobertura, stock de seguridad, punto de pedido y cantidad sugerida. Usa consulta_odoo / agregado_odoo solo para lo que el panel no tiene (pedidos concretos, proveedores, movimientos, fabricaciones, clientes...).
- Las consultas a Odoo son de solo lectura. No puedes crear ni modificar nada en Odoo: si algo requiere una acción (pedir, ajustar, cambiar una regla), recomiéndala y di quién debe hacerla.
- Cuando el usuario pida que le avises de algo, que vigiles algo o crear una alerta, usa proponer_alerta; la regla queda como propuesta y el usuario la guarda con un botón. No digas que está guardada.
- Distingue hechos de estimaciones: la previsión es estadística (Holt amortiguado / Croston-SBA, con su precisión de backtest); avisa si la precisión es baja (< 0,5) o la historia corta.
- La demanda incluye ventas y consumo en fabricación. Cobertura = stock a mano / demanda diaria prevista. Plazo de entrega = el del proveedor en Odoo o el valor por defecto.
- Si el panel está en MODO DEMO, menciónalo una vez: los datos no son en vivo."""


def system_prompt() -> str:
    ctx = (config.load_settings().get("contexto_empresa") or "").strip()
    return SYSTEM_BASE + (f"\n\nContexto de la empresa:\n{ctx}" if ctx else "")


TOOLS = [
    {"name": "resumen_inventario",
     "description": "KPIs globales del inventario (valor, rotación, exceso, stock muerto, precisión de previsión, alertas por severidad, cobertura por tramos, matriz ABC/XYZ) y las alertas más importantes sin revisar.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "buscar_productos",
     "description": "Busca productos y devuelve sus métricas (stock, previsto, demanda, previsión 30/90 d, cobertura, fecha de rotura, plazo, stock de seguridad, punto de pedido, cantidad sugerida, ABC/XYZ, días sin movimiento). texto filtra por referencia, nombre o categoría (vacío = todos).",
     "input_schema": {"type": "object", "properties": {
         "texto": {"type": "string", "description": "Fragmento de referencia, nombre o categoría. Vacío para todos."},
         "ordenar_por": {"type": "string", "enum": ["coverage_days", "value", "forecast_30d", "suggested_order", "days_idle", "demand_52w"],
                         "description": "Campo de orden. coverage_days ascendente, el resto descendente."},
         "limite": {"type": "integer", "minimum": 1, "maximum": 100}},
         "additionalProperties": False}},
    {"name": "detalle_producto",
     "description": "Detalle de un producto por referencia exacta: historia de demanda (26 semanas), previsión semanal con banda del 80 %, proyección de stock, stock por ubicación/lote, compras pendientes y reglas de reabastecimiento.",
     "input_schema": {"type": "object", "properties": {"referencia": {"type": "string"}}, "required": ["referencia"],
                      "additionalProperties": False}},
    {"name": "listar_alertas",
     "description": "Alertas activas del panel, ordenadas por severidad e impacto.",
     "input_schema": {"type": "object", "properties": {
         "severidad": {"type": "string", "enum": ["critica", "alta", "media", "baja"]},
         "tipo": {"type": "string", "description": "rotura, riesgo_rotura, bajo_minimo, punto_pedido, exceso, sin_movimiento, negativo, anomalia, compra_retrasada, caducidad, reglaN"},
         "incluir_revisadas": {"type": "boolean"},
         "limite": {"type": "integer", "minimum": 1, "maximum": 200}},
         "additionalProperties": False}},
    {"name": "consulta_odoo",
     "description": "search_read de SOLO LECTURA en Odoo 18. Modelos permitidos: " + ", ".join(sorted(ODOO_MODELS)) +
                    ". domain en sintaxis Odoo, p. ej. [[\"state\",\"=\",\"purchase\"],[\"date_order\",\">=\",\"2026-01-01\"]]. Indica siempre fields.",
     "input_schema": {"type": "object", "properties": {
         "model": {"type": "string"}, "domain": {"type": "array", "items": {}, "description": "Dominio de Odoo: lista de condiciones [campo, operador, valor] y operadores \"|\" / \"&\""}, "fields": {"type": "array", "items": {"type": "string"}},
         "limit": {"type": "integer", "minimum": 1, "maximum": 200}, "order": {"type": "string"}},
         "required": ["model", "fields"], "additionalProperties": False}},
    {"name": "agregado_odoo",
     "description": "read_group de SOLO LECTURA en Odoo 18 (mismos modelos que consulta_odoo). groupby p. ej. [\"product_id\", \"date:month\"]; measures p. ej. [\"product_qty:sum\", \"price_subtotal:sum\"].",
     "input_schema": {"type": "object", "properties": {
         "model": {"type": "string"}, "domain": {"type": "array", "items": {}, "description": "Dominio de Odoo: lista de condiciones [campo, operador, valor] y operadores \"|\" / \"&\""},
         "groupby": {"type": "array", "items": {"type": "string"}}, "measures": {"type": "array", "items": {"type": "string"}}},
         "required": ["model", "groupby", "measures"], "additionalProperties": False}},
    {"name": "proponer_alerta",
     "description": "Propone una regla de alerta personalizada para que el usuario la guarde. Se dispara para cada producto del ámbito cuyo valor de metric cumpla op threshold. Métricas: " +
                    "; ".join(f"{k} = {v}" for k, v in alerts_mod.METRICS.items()),
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Título corto de la alerta"},
         "description": {"type": "string", "description": "Acción recomendada cuando salte"},
         "scope_type": {"type": "string", "enum": ["all", "category", "products"]},
         "scope_values": {"type": "array", "items": {"type": "string"},
                          "description": "Nombres de categoría o referencias exactas de producto según scope_type"},
         "metric": {"type": "string", "enum": list(alerts_mod.METRICS)},
         "op": {"type": "string", "enum": list(alerts_mod.OPS)},
         "threshold": {"type": "number"},
         "severity": {"type": "string", "enum": alerts_mod.SEVERITIES}},
         "required": ["name", "scope_type", "scope_values", "metric", "op", "threshold", "severity"],
         "additionalProperties": False}},
]


class AIUnavailable(Exception):
    pass


# Modelos de Claude que aceptan effort y el respaldo de modelo del servidor
_CLAUDE_EFFORT = ("claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-haiku-5")
_CLAUDE_FALLBACK = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")


def ai_available() -> bool:
    return credentials.ai_configured()


def _conn():
    c = credentials.get()
    if not credentials.ai_configured(c):
        raise AIUnavailable("Configura el proveedor de IA (botón de conexión, arriba a la derecha)")
    return c


def openai_client(c) -> openai.OpenAI:
    headers = {"HTTP-Referer": APP_REFERER, "X-Title": "Panel de inventario"} if c["ai_provider"] == "openrouter" else None
    return openai.OpenAI(api_key=c["ai_key"] or "no-key", base_url=credentials.base_url_for(c),
                         default_headers=headers, timeout=180, max_retries=2)


def anthropic_client(c) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=c["ai_key"], timeout=600, max_retries=2)


# ----------------------------------------------------------------------------- estado
def _state():
    s = config.load_settings()
    snap = data.get_snapshot()
    metrics = analytics.compute(snap, s)
    al = alerts_mod.evaluate(metrics, snap, s)
    return s, snap, metrics, al


_COMPACT = ["ref", "name", "categ", "uom", "on_hand", "virtual", "incoming", "outgoing", "cost", "value",
            "demand_13w", "demand_52w", "forecast_30d", "forecast_90d", "coverage_days", "coverage_virtual_days",
            "stockout_date", "lead_time_days", "supplier", "safety_stock", "reorder_point", "suggested_order",
            "orderpoint_min", "orderpoint_max", "abc", "xyz", "days_idle", "pending_po_qty", "method", "accuracy"]


def _compact(r):
    return {k: r.get(k) for k in _COMPACT}


def _dump(obj):
    txt = json.dumps(obj, ensure_ascii=False, default=str)
    if len(txt) > MAX_TOOL_CHARS:
        txt = txt[:MAX_TOOL_CHARS] + f"... [recortado: {len(txt)} caracteres en total; afina la consulta]"
    return txt


def run_tool(name, args, ctx):
    s, snap, metrics, al = ctx["state"]
    if name == "resumen_inventario":
        ov = analytics.overview(metrics, [a for a in al if not a["acked"]])
        return {"fuente": snap["source"], "generado": snap["generated_at"], "avisos_datos": snap["warnings"],
                "kpis": ov, "alertas_top": [a for a in al if not a["acked"]][:25]}
    if name == "buscar_productos":
        t = (args.get("texto") or "").lower()
        rows = [r for r in metrics.values()
                if not t or t in r["ref"].lower() or t in r["name"].lower() or t in r["categ"].lower()]
        key = args.get("ordenar_por") or "value"
        if key == "coverage_days":
            rows.sort(key=lambda r: (r["coverage_days"] is None, r["coverage_days"] or 0))
        else:
            rows.sort(key=lambda r: r.get(key) or 0, reverse=True)
        lim = args.get("limite") or 25
        return {"total": len(rows), "productos": [_compact(r) for r in rows[:lim]]}
    if name == "detalle_producto":
        ref = args["referencia"].strip().lower()
        pid = next((r["id"] for r in metrics.values() if r["ref"].lower() == ref), None)
        if pid is None:
            cands = [r["ref"] for r in metrics.values() if ref in r["ref"].lower() or ref in r["name"].lower()][:10]
            return {"error": f"No hay producto con referencia '{args['referencia']}'", "parecidos": cands}
        d = analytics.product_detail(snap, s, pid, metrics)
        h = d["history"]
        return {"metricas": _compact(d["metrics"]), "usado_en": d["metrics"]["used_in"],
                "historia_26s": dict(zip(h["weeks"][-26:], h["values"][-26:])),
                "prevision": {"metodo": d["forecast"]["method"], "precision_backtest": d["forecast"]["accuracy"],
                              "semanas": dict(zip(d["forecast"]["future_weeks"], [round(x, 1) for x in d["forecast"]["forecast"]])),
                              "banda80_bajo": [round(x, 1) for x in d["forecast"]["lo"]],
                              "banda80_alto": [round(x, 1) for x in d["forecast"]["hi"]]},
                "proyeccion_stock_semanal": d["projection"], "stock_por_ubicacion": d["quants"],
                "compras_pendientes": d["pending_po"], "reglas_reabastecimiento": d["orderpoints"]}
    if name == "listar_alertas":
        res = [a for a in al if args.get("incluir_revisadas") or not a["acked"]]
        if args.get("severidad"):
            res = [a for a in res if a["severity"] == args["severidad"]]
        if args.get("tipo"):
            res = [a for a in res if a["kind"] == args["tipo"]]
        return {"total": len(res), "alertas": res[: args.get("limite") or 50]}
    if name in ("consulta_odoo", "agregado_odoo"):
        if snap["source"] != "odoo":
            return {"error": "Sin conexión con Odoo (modo demo o credenciales caducadas). Usa las herramientas del panel."}
        model = args.get("model")
        if model not in ODOO_MODELS:
            return {"error": f"Modelo '{model}' no permitido. Permitidos: {sorted(ODOO_MODELS)}"}
        o = data.client()
        if name == "consulta_odoo":
            rows = o.search_read(model, args.get("domain") or [], args.get("fields") or ["display_name"],
                                 limit=min(int(args.get("limit") or 50), 200), order=args.get("order") or None)
            return {"registros": rows, "n": len(rows)}
        rows = o.read_group(model, args.get("domain") or [], args["measures"], args["groupby"], lazy=False)
        for r in rows:
            r.pop("__domain", None)
        return {"grupos": rows[:300], "n": len(rows)}
    if name == "proponer_alerta":
        rule = alerts_mod.validate_rule(args)
        ctx["proposals"].append(rule)
        # Previsualizar a qué productos afectaría hoy
        hits = []
        vals = {v.lower() for v in rule["scope_values"]}
        for r in metrics.values():
            if rule["scope_type"] == "category" and not any(v in r["categ"].lower() for v in vals):
                continue
            if rule["scope_type"] == "products" and r["ref"].lower() not in vals:
                continue
            v = r.get(rule["metric"])
            if v is not None and alerts_mod.OPS[rule["op"]](float(v), rule["threshold"]):
                hits.append(r["ref"])
        return {"propuesta": rule, "saltaria_hoy_en": hits[:30], "n_productos_hoy": len(hits),
                "nota": "Propuesta pendiente de que el usuario pulse Guardar."}
    return {"error": f"Herramienta desconocida: {name}"}



# ----------------------------------------------------------------------------- bucles
def _exec_tools(calls, ctx):
    """calls: [(id, nombre, args_dict | None, error_de_parseo | None)] → [(id, contenido, es_error)]"""
    out = []
    for cid, name, args, perr in calls:
        if perr:
            out.append((cid, f"Error: argumentos no válidos ({perr}). Reenvía la llamada con JSON correcto.", True))
            continue
        ctx["queries"].append({"tool": name, "input": args})
        try:
            res = run_tool(name, args, ctx)
            out.append((cid, _dump(res), isinstance(res, dict) and "error" in res))
        except Exception as e:
            log.warning("Herramienta %s falló: %s", name, e)
            out.append((cid, f"Error: {type(e).__name__}: {str(e)[:500]}", True))
    return out


def _openai_tools():
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                               "parameters": t["input_schema"]}} for t in TOOLS]


def _run_openai(c, messages, ctx):
    client = openai_client(c)
    is_openrouter = c["ai_provider"] == "openrouter"
    msgs = [{"role": "system", "content": system_prompt()}] + list(messages)
    limit_kw = {"max_completion_tokens": 8000} if c["ai_provider"] == "openai" else {"max_tokens": 8000}
    extra = {"usage": {"include": True}} if is_openrouter else None
    for _ in range(MAX_TURNS):
        resp = client.chat.completions.create(model=c["ai_model"], messages=msgs, tools=_openai_tools(),
                                              tool_choice="auto", extra_body=extra, **limit_kw)
        if not resp.choices:
            err = (getattr(resp, "model_extra", None) or {}).get("error") or "respuesta vacía"
            raise RuntimeError(f"El proveedor no devolvió respuesta: {err}")
        ctx["model"] = resp.model or c["ai_model"]
        u = resp.usage
        if u:
            ctx["usage"]["prompt_tokens"] += u.prompt_tokens or 0
            ctx["usage"]["completion_tokens"] += u.completion_tokens or 0
            cost = getattr(u, "cost", None)
            if cost is None:
                cost = (getattr(u, "model_extra", None) or {}).get("cost")
            if cost is not None:
                ctx["usage"]["cost"] = (ctx["usage"]["cost"] or 0) + float(cost)
        choice = resp.choices[0]
        msg = choice.message
        if not msg.tool_calls:
            text = (msg.content or "").strip()
            if choice.finish_reason == "length":
                text += "\n\n_(Respuesta cortada por longitud.)_"
            return text or "_(El modelo no devolvió texto.)_"
        msgs.append({"role": "assistant", "content": msg.content or "",
                     "tool_calls": [{"id": tc.id, "type": "function",
                                     "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                                    for tc in msg.tool_calls]})
        calls = []
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                calls.append((tc.id, tc.function.name, args if isinstance(args, dict) else {}, None))
            except json.JSONDecodeError as e:
                calls.append((tc.id, tc.function.name, None, str(e)))
        for cid, content, _err in _exec_tools(calls, ctx):
            msgs.append({"role": "tool", "tool_call_id": cid, "content": content})
    return "He alcanzado el límite de consultas para esta pregunta. Prueba a acotarla."


def _run_anthropic(c, messages, ctx, effort):
    client = anthropic_client(c)
    model = c["ai_model"]
    kw = {}
    if model.startswith(_CLAUDE_EFFORT):
        kw["output_config"] = {"effort": effort}
    if model in _CLAUDE_FALLBACK:
        kw.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    msgs = list(messages)
    for _ in range(MAX_TURNS):
        resp = client.beta.messages.create(model=model, max_tokens=16000, system=system_prompt(), tools=TOOLS,
                                           messages=msgs, cache_control={"type": "ephemeral"}, **kw)
        ctx["model"] = resp.model
        ctx["usage"]["prompt_tokens"] += resp.usage.input_tokens or 0
        ctx["usage"]["completion_tokens"] += resp.usage.output_tokens or 0
        if resp.stop_reason == "refusal":
            return "La IA no ha podido responder a esta petición. Reformúlala, por favor."
        msgs.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not tool_uses:
            text = "\n\n".join(b.text for b in resp.content if b.type == "text").strip()
            if resp.stop_reason == "max_tokens":
                text += "\n\n_(Respuesta cortada por longitud.)_"
            return text
        calls = [(tu.id, tu.name, tu.input if isinstance(tu.input, dict) else {}, None) for tu in tool_uses]
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": content, **({"is_error": True} if err else {})}
            for cid, content, err in _exec_tools(calls, ctx)]})
    return "He alcanzado el límite de consultas para esta pregunta. Prueba a acotarla."


def run_agent(messages, effort="medium"):
    """Bucle de herramientas. messages: lista [{role, content}] solo texto. Devuelve dict."""
    c = _conn()
    ctx = {"state": _state(), "proposals": [], "queries": [], "model": c["ai_model"],
           "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": None}}
    kind = credentials.PROVIDERS[c["ai_provider"]]["kind"]
    text = _run_anthropic(c, messages, ctx, effort) if kind == "anthropic" else _run_openai(c, messages, ctx)
    usage = ctx["usage"]
    alerts_mod.record_usage(c["ai_provider"], ctx["model"], usage["prompt_tokens"], usage["completion_tokens"], usage["cost"])
    return {"text": text, "proposals": ctx["proposals"], "queries": ctx["queries"], "model": ctx["model"],
            "provider": c["ai_provider"], "usage": usage}


SUMMARY_PROMPT = """Hoy es {hoy}. Prepara el resumen ejecutivo del inventario para el responsable de Compras y Logística.

Estructura (Markdown, máximo unas 400 palabras, sin relleno):
## Situación
3–5 cifras clave con una frase de lectura (valor de stock, rotación, cobertura, precisión de la previsión).
## Riesgos de rotura
Productos que se quedan sin stock antes de que pueda llegar una compra: fecha estimada, qué cubren las entradas previstas.
## Qué pedir
Tabla: referencia · producto · cantidad sugerida · motivo. Solo lo urgente o importante.
## Dinero parado
Exceso y stock sin movimiento con mayor valor, y qué hacer.
## Señales a vigilar
Anomalías de demanda, compras retrasadas, caducidades y problemas de datos (p. ej., productos sin coste o sin proveedor).

Consulta las herramientas que necesites (empieza por resumen_inventario) y, si hace falta confirmar algo, pregunta directamente a Odoo."""


def summary():
    res = run_agent([{"role": "user", "content": SUMMARY_PROMPT.format(hoy=date.today().isoformat())}], effort="high")
    snap = data.get_snapshot()
    alerts_mod.save_summary("resumen", res["text"], res["model"], snap["source"])
    return res


def chat(history):
    hoy = f"(Fecha de hoy: {date.today().isoformat()})\n\n"
    msgs = []
    for i, m in enumerate(history[-20:]):
        role = "assistant" if m.get("role") == "assistant" else "user"
        content = str(m.get("content") or "")[:8000]
        if i == 0 and role == "user":
            content = hoy + content
        msgs.append({"role": role, "content": content})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs:
        raise ValueError("Mensaje vacío")
    return run_agent(msgs, effort="medium")
