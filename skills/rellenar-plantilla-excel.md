---
name: rellenar-plantilla-excel
description: Rellenar un Excel que el usuario ha adjuntado (hoja de pedido, plantilla de cliente o proveedor) con datos del panel o del ERP, conservando su formato.
---
# Rellenar una plantilla Excel adjunta

## Pasos
1. Lee la plantilla con `leer_documento` (todas las hojas que importen). Identifica:
   - la fila de cabecera y qué significa cada columna (p. ej. "Talla S" = stock de la talla S, "Uds" = casilla
     para que el cliente escriba su pedido → déjala vacía salvo que el usuario diga lo contrario);
   - dónde empiezan las filas de datos y si hay títulos de grupo o celdas combinadas (no escribas en celdas
     combinadas salvo en la superior izquierda);
   - validaciones visibles (p. ej. textos de máx. 35 caracteres).
2. Si alguna columna es ambigua, pregunta antes de rellenar. Si está claro, explica en una línea cómo vas a mapear.
3. Obtén los datos con las herramientas del panel (buscar_productos, detalle_producto: incluye tallas) o el ERP.
4. Escribe con `rellenar_excel`: lista de cambios {hoja, celda, valor}. Usa `estilo_desde_fila` con la primera
   fila de datos de la plantilla para que las filas nuevas hereden su formato.
   - Números como números (sin unidades); tallas inexistentes en el producto → "—".
   - Stock negativo en el ERP → escribe 0 y dilo en tu respuesta.
5. Comprueba: suma por producto = stock del panel; ningún producto pedido se ha quedado fuera.
6. Responde con un resumen breve: productos rellenados, unidades totales, incidencias (negativos, tallas raras,
   referencias no encontradas). El botón para guardar el Excel aparece solo.
