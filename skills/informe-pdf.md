---
name: informe-pdf
description: Preparar un informe en PDF (situación del inventario, riesgos de rotura, qué pedir, análisis de un producto o de un documento adjunto) para compartir o imprimir.
---
# Informe en PDF

1. Reúne los datos con las herramientas (resumen_inventario, listar_alertas, buscar_productos, detalle_producto,
   o el documento adjunto con leer_documento). Las cifras salen de ahí, nunca de memoria.
2. Estructura con `crear_pdf` y bloques:
   - "titulo" para cada apartado; "texto" para 2–4 frases de lectura; "lista" para conclusiones o acciones;
   - "tabla" con columnas cortas (máx. 8; si hay más, el PDF sale en horizontal) y como mucho ~40 filas:
     si hay más, prioriza y dilo.
3. Orden recomendado: Resumen (3–5 cifras) → Riesgos → Acciones recomendadas → Detalle en tablas.
4. Indica la fuente ({ERP}, fecha de los datos) en el texto inicial y marca las estimaciones como tales.
5. En el chat, resume en 2–3 líneas qué contiene. El botón para guardar el PDF aparece solo.
