---
name: excel-stock
description: Preparar un Excel con stock, previsión u otros datos de uno o varios productos (por referencias, categoría, colección o filtro), también por talla.
---
# Excel de stock y datos de producto

Objetivo: entregar un Excel descargable con cifras EXACTAS del panel. Nunca copies cifras a mano: usa la herramienta
`exportar_productos_excel`, que toma los datos directamente del panel.

## Pasos
1. Aclara solo lo imprescindible. Si el usuario no dice qué columnas quiere, usa por defecto:
   referencia, producto, stock a mano, stock previsto, previsión 30 días, cobertura y rotura estimada.
   Si pide "stock disponible", usa `on_hand` (y `free` en Odoo si pide "libre" o "sin reservar").
2. Traduce lo que pide a `campos` (claves): ref, name, categ, family, uom, on_hand, free, virtual, incoming,
   outgoing, cost, price, value, demand_4w, demand_13w, demand_52w, forecast_30d, forecast_90d, coverage_days,
   stockout_date, lead_time_days, supplier, safety_stock, reorder_point, suggested_order, orderpoint_min,
   orderpoint_max, pending_po_qty, abc, xyz, days_idle, last_move, revenue_52w, returns_52w, sizes_out.
3. Selección de productos:
   - Lista de referencias → `referencias` (en el orden que te dé el usuario; respeta ese orden).
   - "Todos los de X" → `texto`, `categoria` o `familia` (en Holded, familia = colección: FUJI, WAVE…).
   - "Solo con stock" → `solo_con_stock: true`.
4. Tallas (Holded, productos con variantes): si pide stock por talla usa `por_talla: "columnas"`
   (una columna por talla, como las hojas de pedido) o `"filas"` (una fila por talla, para filtrar).
5. Pon un `nombre_archivo` claro, p. ej. "Stock FUJI 2026-11-20".
6. Tras crearlo, responde con 2–4 líneas: qué contiene (nº de productos/filas, columnas) y avisa de las
   referencias no encontradas si las hay. El botón para guardarlo aparece solo: no inventes enlaces.

## Si pide algo que el panel no tiene
Consulta el ERP (consulta_odoo / consulta_holded), construye las filas y usa `crear_excel`. Revisa que los
totales cuadren antes de entregarlo y di de dónde sale cada columna.
