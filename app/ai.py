"""Asistente de IA del panel de inventario (multiproveedor).

Proveedores:
  - compatibles con la API de OpenAI (OpenRouter, OpenAI, servidores locales como Ollama/LM Studio)
  - Anthropic (Claude)

El modelo recibe herramientas de SOLO LECTURA:
  - métricas ya calculadas por el panel (rápidas, sin tocar el ERP)
  - consultas directas al ERP de la sección: Odoo (search_read / read_group con OdooReadOnly) u
    Holded (GET con HoldedReadOnly), siempre de solo lectura y sobre una lista blanca
  - proponer_alerta: valida una regla de alerta y la devuelve como PROPUESTA; solo se guarda
    si el usuario pulsa "Guardar" en el panel.
"""

import json
import logging
from datetime import date

import anthropic
import openai

from . import alerts as alerts_mod
from . import analytics, config, credentials, data, files, holded, integrations, skills

log = logging.getLogger(__name__)
MAX_TOOL_CHARS = 30000
MAX_TURNS = 20
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
- Empieza por las herramientas del panel (resumen_inventario, buscar_productos, detalle_producto, listar_alertas): ya traen previsión, cobertura, stock de seguridad, punto de pedido y cantidad sugerida. Usa la consulta directa al ERP ({ERP_TOOLS}) solo para lo que el panel no tiene (pedidos concretos, proveedores, movimientos, fabricaciones, clientes...).
- Los datos vienen de {ERP} y las consultas son de solo lectura. No puedes crear ni modificar nada en {ERP}: si algo requiere una acción (pedir, ajustar, cambiar un mínimo), recomiéndala y di quién debe hacerla.
- Gestión de alertas: el usuario decide qué alertas quiere, de qué productos y con qué severidad. Antes de cambiar nada, consulta listar_reglas_alerta.
  · Crear una alerta personalizada → proponer_alerta. Modificarla → proponer_modificar_alerta. Eliminarla → proponer_eliminar_alerta.
  · Alertas automáticas (sin stock, exceso, caducidad…): activar/desactivar un tipo, cambiar su severidad o dejar de avisar de productos, categorías o familias de referencia (prefijo: BOX, ENV, MC…) → proponer_config_automatica. Para no avisar de un producto en ninguna alerta usa kind "*".
  · Todo queda como PROPUESTA: el usuario la aplica con un botón. Nunca digas que ya está hecho; di qué cambiará al aplicarla.
- Distingue hechos de estimaciones: la previsión es estadística (Holt amortiguado / Croston-SBA, con su precisión de backtest); avisa si la precisión es baja (< 0,5) o la historia corta.
- La demanda incluye ventas y consumo en fabricación. Cobertura = stock a mano / demanda diaria prevista. Plazo de entrega = el del proveedor en el ERP o el valor por defecto.{ERP_NOTES}
- Documentos: el usuario puede adjuntar Excel, CSV o PDF (verás «Documentos adjuntos» con su id): léelos con leer_documento.
  Puedes entregar archivos descargables: Excel de productos con cifras del panel → exportar_productos_excel (preferible: no
  transcribas cifras a mano); Excel libre → crear_excel; rellenar un Excel adjunto conservando su formato → rellenar_excel;
  informe → crear_pdf. Cuando generes un archivo, la app muestra el botón para guardarlo: no escribas enlaces ni rutas.
- Si el panel está en MODO DEMO, menciónalo una vez: los datos no son en vivo."""


ERP_NOTES = {
    "odoo": "",
    "holded": (" En Holded: la demanda sale de los albaranes, neta de devoluciones (y del consumo de las órdenes de producción "
               "terminadas); el mínimo es el «stock mínimo» del producto; la categoría es la etiqueta del producto o, si no "
               "tiene, el tipo deducido del nombre (Camisetas, Shorts, Tirantes…); «family» es la COLECCIÓN (FUJI, WAVE…); "
               "el stock y la demanda también están por TALLA (sizes, sizes_out = tallas agotadas con demanda); los canales "
               "de venta salen de las etiquetas de los albaranes (tienda, decathlon, club…). Holded no guarda plazos de "
               "proveedor (se usa el plazo por defecto) y si faltan costes el valor en € sale a cero: dilo y usa unidades o "
               "facturación (revenue_52w)."),
}


def system_prompt() -> str:
    sec = config.section()
    tools = "consulta_odoo / agregado_odoo" if sec == "odoo" else "consulta_holded"
    base = (SYSTEM_BASE.replace("{ERP_TOOLS}", tools).replace("{ERP_NOTES}", ERP_NOTES[sec])
            .replace("{ERP}", config.erp_name()))
    ctx = (config.load_settings().get("contexto_empresa") or "").strip()
    return base + skills.prompt_section() + (f"\n\nContexto de la empresa:\n{ctx}" if ctx else "")


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
         "scope_type": {"type": "string", "enum": ["all", "category", "family", "products"]},
         "scope_values": {"type": "array", "items": {"type": "string"},
                          "description": "Según scope_type: nombres de categoría, familias de referencia (prefijo antes del guion: BOX, ENV, MC…) o referencias exactas de producto"},
         "metric": {"type": "string", "enum": list(alerts_mod.METRICS)},
         "op": {"type": "string", "enum": list(alerts_mod.OPS)},
         "threshold": {"type": "number"},
         "severity": {"type": "string", "enum": alerts_mod.SEVERITIES}},
         "required": ["name", "scope_type", "scope_values", "metric", "op", "threshold", "severity"],
         "additionalProperties": False}},
    {"name": "listar_reglas_alerta",
     "description": "Configuración actual de las alertas: alertas personalizadas (con su id), tipos automáticos (activo, severidad, productos/categorías/familias excluidos, umbrales) y exclusiones globales. Úsala antes de proponer cambios.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "proponer_modificar_alerta",
     "description": "Propone modificar una alerta PERSONALIZADA existente (por su id). Indica solo los campos que cambian; para desactivarla sin borrarla usa active=false.",
     "input_schema": {"type": "object", "properties": {
         "rule_id": {"type": "integer"},
         "name": {"type": "string"}, "description": {"type": "string"},
         "scope_type": {"type": "string", "enum": list(alerts_mod.SCOPES)},
         "scope_values": {"type": "array", "items": {"type": "string"}},
         "metric": {"type": "string", "enum": list(alerts_mod.METRICS)},
         "op": {"type": "string", "enum": list(alerts_mod.OPS)},
         "threshold": {"type": "number"},
         "severity": {"type": "string", "enum": alerts_mod.SEVERITIES},
         "active": {"type": "boolean"}},
         "required": ["rule_id"], "additionalProperties": False}},
    {"name": "proponer_eliminar_alerta",
     "description": "Propone eliminar una alerta PERSONALIZADA (por su id).",
     "input_schema": {"type": "object", "properties": {"rule_id": {"type": "integer"}}, "required": ["rule_id"],
                      "additionalProperties": False}},
    {"name": "proponer_config_automatica",
     "description": "Propone cambiar una alerta AUTOMÁTICA: activarla/desactivarla, fijar su severidad ('auto' = la que calcula el panel) o añadir/quitar productos, categorías o familias excluidos. kind '*' = exclusiones para TODAS las alertas. Tipos: " + ", ".join(f"{k} ({v['label']})" for k, v in alerts_mod.AUTO_KINDS.items()),
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": list(alerts_mod.AUTO_KINDS) + ["*"]},
         "active": {"type": "boolean"},
         "severity": {"type": "string", "enum": ["auto"] + alerts_mod.SEVERITIES},
         "add_exclusions": {"type": "object", "properties": {"products": {"type": "array", "items": {"type": "string"}, "description": "Referencias exactas"}, "categories": {"type": "array", "items": {"type": "string"}, "description": "Categorías de producto (en Holded, la primera etiqueta)"}, "families": {"type": "array", "items": {"type": "string"}, "description": "Prefijos de referencia (BOX, ENV, MC…)"}}, "additionalProperties": False},
         "remove_exclusions": {"type": "object", "properties": {"products": {"type": "array", "items": {"type": "string"}, "description": "Referencias exactas"}, "categories": {"type": "array", "items": {"type": "string"}, "description": "Categorías de producto (en Holded, la primera etiqueta)"}, "families": {"type": "array", "items": {"type": "string"}, "description": "Prefijos de referencia (BOX, ENV, MC…)"}}, "additionalProperties": False}},
         "required": ["kind"], "additionalProperties": False}},
]

HOLDED_TOOL = {
    "name": "consulta_holded",
    "description": "GET de SOLO LECTURA a la API v2 de Holded. path relativo a /api/v2 que empiece por: " +
                   ", ".join(holded.ALLOWED_PREFIXES) + " (p. ej. 'purchase-orders', 'waybills', 'products/{id}/stock', "
                   "'warehouses/{id}/stock', 'contacts/{id}'). params: filtros de la API (start_date, end_date en ISO "
                   "AAAA-MM-DD, status, contact_id, sort, limit ≤ 200, cursor). Importes y unidades de documentos llevan "
                   "coma decimal.",
    "input_schema": {"type": "object", "properties": {
        "path": {"type": "string"},
        "params": {"type": "object", "additionalProperties": {"type": ["string", "number", "boolean"]}}},
        "required": ["path"], "additionalProperties": False}}
ODOO_ONLY = {"consulta_odoo", "agregado_odoo"}

_CELL = {"type": ["string", "number", "boolean", "null"]}
DOC_TOOLS = [
    {"name": "leer_documento",
     "description": "Lee un documento adjunto por su id. Excel: filas con sus celdas por letra de columna (A, B…), para poder "
                    "rellenarlo; indica hoja y desde_fila para paginar. CSV: filas. PDF: texto por página (desde_fila = página).",
     "input_schema": {"type": "object", "properties": {
         "documento_id": {"type": "string"}, "hoja": {"type": "string"},
         "desde_fila": {"type": "integer", "minimum": 1}, "filas": {"type": "integer", "minimum": 1, "maximum": 400}},
         "required": ["documento_id"], "additionalProperties": False}},
    {"name": "exportar_productos_excel",
     "description": "Crea un Excel descargable con datos de productos tomados DIRECTAMENTE del panel (cifras exactas). "
                    "Selecciona por referencias, texto, categoría o familia/colección. campos: " + ", ".join(files.PRODUCT_FIELDS) +
                    ". por_talla (productos con tallas): 'columnas' = una columna por talla; 'filas' = una fila por talla.",
     "input_schema": {"type": "object", "properties": {
         "nombre_archivo": {"type": "string"}, "titulo": {"type": "string"},
         "campos": {"type": "array", "items": {"type": "string", "enum": list(files.PRODUCT_FIELDS)}},
         "referencias": {"type": "array", "items": {"type": "string"}, "description": "Referencias exactas o fragmentos; se respeta el orden"},
         "texto": {"type": "string"}, "categoria": {"type": "string"}, "familia": {"type": "string"},
         "solo_con_stock": {"type": "boolean"}, "por_talla": {"type": "string", "enum": ["no", "columnas", "filas"]},
         "orden": {"type": "string", "description": "Campo por el que ordenar; con '-' delante, descendente (p. ej. '-on_hand')"}},
         "required": ["nombre_archivo", "campos"], "additionalProperties": False}},
    {"name": "crear_excel",
     "description": "Crea un Excel descargable con las hojas, columnas y filas que indiques (para datos que no salen del panel, "
                    "p. ej. resultados de consultas al ERP o de un documento adjunto). Los números deben ir como números.",
     "input_schema": {"type": "object", "properties": {
         "nombre_archivo": {"type": "string"},
         "hojas": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
             "nombre": {"type": "string"}, "titulo": {"type": "string"}, "subtitulo": {"type": "string"},
             "columnas": {"type": "array", "items": {"type": "string"}},
             "filas": {"type": "array", "items": {"type": "array", "items": _CELL}}},
             "required": ["columnas", "filas"], "additionalProperties": False}}},
         "required": ["nombre_archivo", "hojas"], "additionalProperties": False}},
    {"name": "rellenar_excel",
     "description": "Crea una copia de un Excel ADJUNTO escribiendo valores en celdas concretas y conservando su formato "
                    "(plantillas de pedido, hojas de cliente…). estilo_desde_fila: fila de la plantilla cuyo formato heredan las celdas sin formato.",
     "input_schema": {"type": "object", "properties": {
         "documento_id": {"type": "string"}, "nombre_archivo": {"type": "string"},
         "estilo_desde_fila": {"type": "integer", "minimum": 1},
         "cambios": {"type": "array", "items": {"type": "object", "properties": {
             "hoja": {"type": "string"}, "celda": {"type": "string", "description": "p. ej. D10"}, "valor": _CELL},
             "required": ["celda", "valor"], "additionalProperties": False}}},
         "required": ["documento_id", "cambios"], "additionalProperties": False}},
    {"name": "crear_pdf",
     "description": "Crea un informe PDF descargable con bloques: titulo (apartado), texto, lista (elementos) y tabla (columnas, filas).",
     "input_schema": {"type": "object", "properties": {
         "nombre_archivo": {"type": "string"}, "titulo": {"type": "string"},
         "bloques": {"type": "array", "items": {"type": "object", "properties": {
             "tipo": {"type": "string", "enum": ["titulo", "texto", "lista", "tabla"]},
             "texto": {"type": "string"}, "elementos": {"type": "array", "items": {"type": "string"}},
             "columnas": {"type": "array", "items": {"type": "string"}},
             "filas": {"type": "array", "items": {"type": "array", "items": _CELL}}},
             "required": ["tipo"], "additionalProperties": False}}},
         "required": ["nombre_archivo", "titulo", "bloques"], "additionalProperties": False}},
]
SKILL_TOOL = {
    "name": "cargar_skill",
    "description": "Carga las instrucciones de una skill (guía de trabajo) de la lista de skills disponibles.",
    "input_schema": {"type": "object", "properties": {"nombre": {"type": "string"}}, "required": ["nombre"],
                     "additionalProperties": False}}


def tools_for_section():
    base = ([t for t in TOOLS if t["name"] not in ODOO_ONLY] + [HOLDED_TOOL]) if config.section() == "holded" else list(TOOLS)
    base += DOC_TOOLS
    if skills.available():
        base.append(SKILL_TOOL)
    return base + integrations.tools()


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
class _LazyState:
    """Los datos del ERP solo se cargan si alguna herramienta los necesita (leer un PDF no requiere Odoo)."""
    def __init__(self):
        self._v = None

    def __iter__(self):
        if self._v is None:
            self._v = _state()
        return iter(self._v)


def _state():
    s = config.load_settings()
    snap = data.get_snapshot()
    metrics = analytics.compute(snap, s)
    al = alerts_mod.evaluate(metrics, snap, s)
    return s, snap, metrics, al


_COMPACT = ["ref", "name", "categ", "uom", "on_hand", "virtual", "incoming", "outgoing", "cost", "value",
            "demand_13w", "demand_52w", "forecast_30d", "forecast_90d", "coverage_days", "coverage_virtual_days",
            "stockout_date", "lead_time_days", "supplier", "safety_stock", "reorder_point", "suggested_order",
            "orderpoint_min", "orderpoint_max", "abc", "xyz", "days_idle", "pending_po_qty", "method", "accuracy",
            "family", "sizes_out", "sizes_risk", "returns_52w", "return_rate", "revenue_52w", "channels"]


def _compact(r):
    return {k: r.get(k) for k in _COMPACT}


def _dump(obj):
    txt = json.dumps(obj, ensure_ascii=False, default=str)
    if len(txt) > MAX_TOOL_CHARS:
        txt = txt[:MAX_TOOL_CHARS] + f"... [recortado: {len(txt)} caracteres en total; afina la consulta]"
    return txt


def _file_out(ctx, item):
    pub = {k: item[k] for k in ("id", "name", "type", "size")}
    ctx["files"].append(pub)
    extra = {k: item[k] for k in ("filas", "productos", "no_encontrados") if k in item}
    return {"archivo_creado": item["name"], "id": item["id"], **extra,
            "nota": "El usuario ya ve el botón para guardarlo en Descargas."}


def run_tool(name, args, ctx):
    # Herramientas que no necesitan los datos del ERP
    if name == "cargar_skill":
        return skills.load(args.get("nombre", ""))
    if name == "leer_documento":
        return files.read(args["documento_id"], args.get("hoja"), args.get("desde_fila") or 1, args.get("filas") or 150)
    if name == "crear_excel":
        return _file_out(ctx, files.create_excel(args["nombre_archivo"], args.get("hojas") or []))
    if name == "rellenar_excel":
        return _file_out(ctx, files.fill_excel(args["documento_id"], args.get("cambios") or [],
                                               args.get("nombre_archivo"), args.get("estilo_desde_fila")))
    if name == "crear_pdf":
        return _file_out(ctx, files.create_pdf(args["nombre_archivo"], args.get("titulo"), args.get("bloques") or []))
    res = integrations.handle(name, args, ctx)
    if res is not None:
        return res
    s, snap, metrics, al = ctx["state"]
    if name == "exportar_productos_excel":
        return _file_out(ctx, files.export_products(
            metrics, config.erp_name() + (" (demo)" if snap.get("source") == "demo" else ""), args.get("campos") or [],
            args["nombre_archivo"], refs=args.get("referencias"), text=args.get("texto"), category=args.get("categoria"),
            family=args.get("familia"), by_size=args.get("por_talla") or "no", only_stock=bool(args.get("solo_con_stock")),
            title=args.get("titulo"), order=args.get("orden")))
    if name == "resumen_inventario":
        ov = analytics.overview(metrics, [a for a in al if not a["acked"]])
        return {"fuente": snap["source"], "generado": snap["generated_at"], "avisos_datos": snap["warnings"],
                "kpis": ov, "ventas_por_canal_12m": snap.get("channels") or {},
                "alertas_top": [a for a in al if not a["acked"]][:25]}
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
                "tallas": d["metrics"].get("sizes") or [],
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
    if name == "consulta_holded":
        if snap["source"] != "holded":
            return {"error": "Sin conexión con Holded (modo demo o token no válido). Usa las herramientas del panel."}
        path = str(args.get("path") or "").strip().lstrip("/")
        path = path[len("api/v2/"):] if path.startswith("api/v2/") else path
        if not path.startswith(holded.ALLOWED_PREFIXES) or ".." in path or "?" in path:
            return {"error": f"Ruta no permitida. Empieza por: {', '.join(holded.ALLOWED_PREFIXES)}"}
        params = {k: v for k, v in (args.get("params") or {}).items() if isinstance(v, (str, int, float, bool))}
        if "limit" in params:
            params["limit"] = max(1, min(int(float(params["limit"])), 200))
        return data.client().get(path, params)
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
    if name == "listar_reglas_alerta":
        cfgs = alerts_mod.kind_configs()
        return {"personalizadas": alerts_mod.list_rules(),
                "automaticas": {k: {**v, "nombre": alerts_mod.AUTO_KINDS[k]["label"],
                                    "umbrales": {sk: s[sk] for sk in alerts_mod.AUTO_KINDS[k]["settings"]}}
                                for k, v in cfgs.items() if k in alerts_mod.AUTO_KINDS},
                "exclusiones_globales": cfgs[alerts_mod.GLOBAL]["exclude"],
                "familias_disponibles": sorted({r["family"] for r in metrics.values()}),
                "categorias_disponibles": sorted({r["categ"] for r in metrics.values()})}
    if name == "proponer_modificar_alerta":
        rid = int(args["rule_id"])
        before = alerts_mod.get_rule(rid)
        changes = {k: v for k, v in args.items() if k != "rule_id"}
        after = alerts_mod.validate_rule({**before, **changes})  # valida sin guardar
        ctx["proposals"].append({"action": "update", "rule_id": rid, "changes": changes, "before": before, "after": after})
        return {"propuesta": "modificar", "antes": before, "despues": after,
                "nota": "Pendiente de que el usuario pulse Aplicar."}
    if name == "proponer_eliminar_alerta":
        rid = int(args["rule_id"])
        before = alerts_mod.get_rule(rid)
        ctx["proposals"].append({"action": "delete", "rule_id": rid, "before": before})
        return {"propuesta": "eliminar", "alerta": before, "nota": "Pendiente de que el usuario pulse Aplicar."}
    if name == "proponer_config_automatica":
        kind = args["kind"]
        if kind not in alerts_mod.AUTO_KINDS and kind != alerts_mod.GLOBAL:
            return {"error": f"Tipo no válido: {kind}"}
        if kind == alerts_mod.GLOBAL and ("active" in args or "severity" in args):
            return {"error": "Para '*' solo se pueden cambiar exclusiones"}
        p = {"action": "auto_config", "kind": kind, "label": alerts_mod.AUTO_KINDS.get(kind, {}).get("label", "Todas las alertas"),
             **{k: args[k] for k in ("active", "severity", "add_exclusions", "remove_exclusions") if k in args}}
        if not any(k in p for k in ("active", "severity", "add_exclusions", "remove_exclusions")):
            return {"error": "No hay ningún cambio que proponer"}
        ctx["proposals"].append(p)
        return {"propuesta": p, "actual": alerts_mod.kind_configs()[kind], "nota": "Pendiente de que el usuario pulse Aplicar."}
    if name == "proponer_alerta":
        rule = alerts_mod.validate_rule(args)
        ctx["proposals"].append({"action": "create", "rule": rule, **rule})
        # Previsualizar a qué productos afectaría hoy
        hits = []
        vals = {v.lower() for v in rule["scope_values"]}
        for r in metrics.values():
            if rule["scope_type"] == "category" and not any(v in r["categ"].lower() for v in vals):
                continue
            if rule["scope_type"] == "products" and r["ref"].lower() not in vals:
                continue
            if rule["scope_type"] == "family" and r["family"].lower() not in vals:
                continue
            v = r.get(rule["metric"])
            if v is not None and alerts_mod.OPS[rule["op"]](float(v), rule["threshold"]):
                hits.append(r["ref"])
        return {"propuesta": rule, "saltaria_hoy_en": hits[:30], "n_productos_hoy": len(hits),
                "nota": "Pendiente de que el usuario pulse Aplicar."}
    return {"error": f"Herramienta desconocida: {name}"}



# ----------------------------------------------------------------------------- bucles
def _exec_tools(calls, ctx):
    """calls: [(id, nombre, args_dict | None, error_de_parseo | None)] → [(id, contenido, es_error)]"""
    out = []
    for cid, name, args, perr in calls:
        if perr:
            out.append((cid, f"Error: argumentos no válidos ({perr}). Reenvía la llamada con JSON correcto.", True))
            continue
        shown = args if len(json.dumps(args, ensure_ascii=False, default=str)) < 600 else \
            {k: (v if len(json.dumps(v, ensure_ascii=False, default=str)) < 120 else "…") for k, v in args.items()}
        ctx["queries"].append({"tool": name, "input": shown})
        try:
            res = run_tool(name, args, ctx)
            out.append((cid, _dump(res), isinstance(res, dict) and "error" in res))
        except Exception as e:
            log.warning("Herramienta %s falló: %s", name, e)
            out.append((cid, f"Error: {type(e).__name__}: {str(e)[:500]}", True))
    return out


def _openai_tools():
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                               "parameters": t["input_schema"]}} for t in tools_for_section()]


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
        resp = client.beta.messages.create(model=model, max_tokens=16000, system=system_prompt(), tools=tools_for_section(),
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
    ctx = {"state": _LazyState(), "proposals": [], "queries": [], "files": [], "model": c["ai_model"],
           "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": None}}
    kind = credentials.PROVIDERS[c["ai_provider"]]["kind"]
    text = _run_anthropic(c, messages, ctx, effort) if kind == "anthropic" else _run_openai(c, messages, ctx)
    usage = ctx["usage"]
    alerts_mod.record_usage(c["ai_provider"], ctx["model"], usage["prompt_tokens"], usage["completion_tokens"], usage["cost"])
    return {"text": text, "proposals": ctx["proposals"], "queries": ctx["queries"], "files": ctx["files"],
            "model": ctx["model"], "provider": c["ai_provider"], "usage": usage}


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

Consulta las herramientas que necesites (empieza por resumen_inventario) y, si hace falta confirmar algo, consulta directamente el ERP."""


def summary():
    res = run_agent([{"role": "user", "content": SUMMARY_PROMPT.format(hoy=date.today().isoformat())}], effort="high")
    snap = data.get_snapshot()
    alerts_mod.save_summary("resumen", res["text"], res["model"], snap["source"])
    return res


def _attachments_note(refs) -> str:
    lines = []
    for r in refs or []:
        try:
            f = files.get(str(r.get("id") if isinstance(r, dict) else r))
        except files.FileError:
            continue
        lines.append(f"- «{f['name']}» (id: {f['id']}, {f['type']}; {f.get('summary') or ''})")
    return ("\n\n[Documentos adjuntos — léelos con leer_documento]\n" + "\n".join(lines)) if lines else ""


def chat(history):
    hoy = f"(Fecha de hoy: {date.today().isoformat()})\n\n"
    msgs = []
    for i, m in enumerate(history[-20:]):
        role = "assistant" if m.get("role") == "assistant" else "user"
        content = str(m.get("content") or "")[:8000]
        if role == "user":
            content += _attachments_note(m.get("files"))
        elif m.get("files"):
            content += "\n\n[Archivos generados en esta respuesta: " + ", ".join(
                f"«{f.get('name')}» (id: {f.get('id')})" for f in m["files"]) + "]"
        if i == 0 and role == "user":
            content = hoy + content
        msgs.append({"role": role, "content": content})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs:
        raise ValueError("Mensaje vacío")
    return run_agent(msgs, effort="medium")
