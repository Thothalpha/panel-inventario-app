---
name: propuesta-compra
description: Preparar una propuesta de pedido de compra (qué pedir, cuánto y a qué proveedor) en Excel, a partir de cantidades sugeridas, mínimos y previsión.
---
# Propuesta de compra en Excel

1. Parte de los productos con cantidad sugerida > 0 (buscar_productos ordenado por suggested_order) y de las
   alertas de rotura, riesgo de rotura, bajo mínimo y punto de pedido.
2. Si el usuario limita por proveedor, categoría o colección, filtra; si da un presupuesto, prioriza por riesgo
   (rotura antes que plazo) y clase ABC.
3. Genera el Excel con `exportar_productos_excel` y estos campos: ref, name, supplier, on_hand, virtual,
   forecast_30d, coverage_days, stockout_date, lead_time_days, reorder_point, suggested_order, cost, pending_po_qty.
   Ordena por coverage_days ascendente (`orden: "coverage_days"`).
4. En el chat: total de líneas, importe estimado (suggested_order × coste, si hay costes) y los 3–5 productos
   más urgentes con su fecha de rotura. Recuerda que es una propuesta: el pedido se lanza en {ERP}.
