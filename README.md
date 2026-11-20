# Panel Inventario

App de escritorio para **macOS (Apple Silicon)** que lee el inventario de **Odoo** y de **Holded** en modo de solo lectura, calcula la
previsión de demanda, la cobertura y el punto de pedido de cada producto, genera **alertas** e incluye un **asistente
de IA** que responde preguntas consultando el ERP en el momento. Tú eliges el proveedor del modelo: OpenRouter, OpenAI,
Anthropic o cualquier servidor compatible con la API de OpenAI.

**Dos secciones independientes, Odoo y Holded**, con un selector en la cabecera. Cada sección tiene su propia conexión con
el ERP, su clave y modelo de IA, sus datos, sus alertas, sus ajustes y su gasto de IA.

## Qué hace

| Pestaña | Contenido |
|---|---|
| **Resumen** | KPIs (valor de stock, rotación, alertas urgentes, a pedir, exceso, stock parado, compras en curso, precisión de la previsión), resumen con IA, cobertura por tramos, alertas por tipo, valor por categoría, matriz ABC/XYZ y mayor demanda prevista |
| **Productos** | Tabla filtrable y ordenable. Cada producto tiene detalle con su demanda semanal y previsión (banda del 80 %), la proyección de stock frente al punto de pedido y al stock de seguridad, el stock por ubicación y lote, las compras pendientes y la lista de materiales |
| **Alertas** | Alertas automáticas y personalizadas, con severidad, acción recomendada e impacto estimado. Filtros por severidad, tipo, categoría, familia de referencia (prefijo: BOX, ENV, MC…) y texto. Tú decides qué alertas quieres: activa o desactiva cada tipo automático, cambia su severidad y umbrales, excluye productos, categorías o familias (de un tipo o de todas las alertas) y crea, edita o elimina tus propias alertas |
| **Asistente IA** | Chat que consulta las métricas del panel y Odoo (solo lectura). Muestra las consultas que ha hecho y el coste de cada respuesta. También gestiona las alertas: propone crearlas, modificarlas, eliminarlas o configurar las automáticas, y tú aplicas cada cambio con un botón |
| **Configuración** | Conexiones, parámetros de previsión y alertas, y contexto de la empresa para el asistente |

**Novedades:** tras cada actualización, la primera vez que abres la app aparece una ventana con lo nuevo (solo una vez).

**Alertas automáticas:**
- sin stock con demanda, y riesgo de rotura antes de que llegue una compra;
- previsto por debajo del mínimo de Odoo o del punto de pedido calculado;
- exceso de stock y stock sin movimiento;
- stock negativo y anomalías de demanda;
- compras retrasadas y lotes caducados o a punto de caducar.

**Previsión:** para cada producto compiten Holt amortiguado, suavizado exponencial simple, media móvil de 13 semanas y
Holt con estacionalidad. Si la demanda es intermitente, compiten Croston-SBA y la media móvil. Gana el método con
menor error (WAPE en bloques de 4 semanas) sobre las últimas 12 semanas. La demanda incluye las ventas y el consumo en
fabricación.

## Instalación en el Mac

Requisitos: Mac con Apple Silicon (M1 o posterior) y macOS 13 o superior.

Abre **Terminal**, pega esta línea y pulsa Intro:

```bash
curl -fsSL https://raw.githubusercontent.com/Thothalpha/panel-inventario-app/main/scripts/instalar-mac.sh | bash
```

El script descarga la última versión publicada, comprueba su suma SHA-256, la instala en Aplicaciones y la abre. Si
prefieres revisarlo antes de ejecutarlo, está en [`scripts/instalar-mac.sh`](scripts/instalar-mac.sh).

> La app aún no está firmada con una cuenta de Apple Developer. Si la descargas con el navegador, macOS la
> bloqueará la primera vez: ve a **Ajustes del Sistema → Privacidad y seguridad → «Abrir igualmente»**. Con el
> script de Terminal no hace falta.

**Actualizar:** cuando hay una versión nueva, la app lo avisa y muestra el mismo comando para copiarlo.

## Configuración (la primera vez, en cada sección)

1. **Odoo:** dirección, base de datos (normalmente se detecta sola), tu usuario (login) y una **API key**. La API key
   se crea en Odoo, en Mi perfil → Seguridad de la cuenta → Nueva API key. Elige una duración larga.
   **Holded:** un token de la **API v2** (empieza por `pat_`), que se crea en Holded → Ajustes → Desarrolladores → API.
   Basta con permisos de lectura de productos, almacenes, albaranes, pedidos de compra, órdenes de producción y
   contactos. Las claves antiguas de la API v1 ya no funcionan.
2. **Asistente IA:** elige proveedor y pega la clave de su API. Con **OpenRouter** se cargan todos sus modelos con el
   precio de entrada y salida; el modelo por defecto es DeepSeek V4 Flash. Los modelos que no pueden usar
   herramientas aparecen desactivados, porque el asistente las necesita para consultar Odoo.

## Cómo se leen los datos de Holded

| En la app | En Holded (API v2) |
|---|---|
| Productos y stock por almacén | Productos con stock (`has_stock`), `stocks` por almacén; archivados excluidos |
| Tipo (categoría) | Primera etiqueta del producto; si no tiene, el tipo deducido del nombre (Camisetas, Shorts, Tirantes…) |
| Colección (familia) | Atributo `COLECCIONES` del producto (FUJI, WAVE…) o el nombre de una colección conocida |
| Tallas | Variantes del producto: stock por variante y demanda por `variant_id` de los albaranes; tallas normalizadas (XS…XXL) |
| Canal de venta | Etiqueta del albarán (tienda, decathlon, club…); las etiquetas con aspecto de identificador se ignoran |
| Coste y valor | `cost` del producto o media de sus variantes. Sin costes, el valor sale a 0 y la clase ABC se calcula por facturación |
| Mínimo del ERP | `min_stock` del producto |
| Lista de materiales | `pack_items` de los packs |
| Demanda | Líneas de **albaranes** (excepto cancelados), **netas de devoluciones** (unidades negativas) + consumo de las órdenes de producción terminadas |
| Compras pendientes | Líneas de pedidos de compra no recibidas (`supplied = false`), con su fecha prevista |
| Plazo de entrega | Holded no lo guarda: se usa el plazo por defecto de Configuración |

Holded permite unas 100 peticiones por minuto: la app las espacia y reintenta si recibe un 429, así que la primera
carga de datos puede tardar algo más que en Odoo.

## Documentos, skills e integraciones del asistente

**Documentos.** En el Asistente puedes adjuntar Excel (.xlsx), CSV o PDF (clip 📎 o arrastrándolos). El asistente
los lee y puede devolverte archivos:

| Herramienta | Para qué |
|---|---|
| `exportar_productos_excel` | Excel de productos con cifras tomadas directamente del panel (stock, previsión, cobertura…, también por talla) |
| `crear_excel` | Excel libre (resultados de consultas al ERP, de un documento adjunto…) |
| `rellenar_excel` | Copia de un Excel adjunto con celdas rellenadas, conservando el formato de la plantilla |
| `crear_pdf` | Informe PDF con apartados, texto, listas y tablas |
| `leer_documento` | Lee un adjunto (Excel con coordenadas de celda, CSV o texto de PDF) |

Los archivos viven en la carpeta de datos de cada sección (60 días) y «Guardar en Descargas» los copia a
`~/Downloads`; desde ahí se pueden abrir o mostrar en el Finder.

**Skills.** Guías de trabajo en Markdown que el asistente carga cuando la tarea lo pide. Las integradas están en
`skills/` y llegan con cada versión; el usuario puede desactivarlas o añadir las suyas en Configuración. Formato:

```markdown
---
name: pedido-decathlon            # minúsculas y guiones
description: Cuándo usarla, en una frase
secciones: holded                 # opcional: odoo, holded (por defecto, las dos)
---
Instrucciones paso a paso. {ERP} se sustituye por Odoo u Holded.
```

**Integraciones.** `app/integrations.py` es la base para conectar otras apps: cada integración registra sus
herramientas y el usuario la activa en Configuración. Reglas: solo lectura por defecto, cualquier cambio en otra
app como propuesta que el usuario aplica, y claves en el Llavero.

**Novedades.** Tras cada actualización, la primera vez que se abre la app aparece una ventana con las novedades
(`app/changelog.py`). Al publicar una versión, añade su entrada al principio de `NOVEDADES`.

## Privacidad y seguridad

- **Solo lectura.** El cliente de Odoo solo permite métodos de lectura y el de Holded solo hace peticiones GET. El asistente solo
  puede consultar una lista blanca de modelos (productos, stock, compras, fabricación y ventas).
- **Claves.** Las claves de Odoo y del proveedor de IA se guardan en el **Llavero de macOS**. El resto de la
  configuración y los datos locales (alertas personalizadas, resúmenes, uso) están en
  `~/Library/Application Support/PanelInventario/`, y el registro en `~/Library/Logs/PanelInventario/`.
- **Sin servidores intermedios.** Las consultas van directamente del Mac a Odoo y al proveedor de IA elegido. Lo que
  preguntes al asistente, y los datos que consulte para responder, se envían a ese proveedor.
- **Servidor local protegido.** La interfaz se sirve desde un servidor en `127.0.0.1` que exige un token aleatorio
  en cada sesión y valida la cabecera `Host`, así que ninguna web abierta en el navegador puede usarlo.

## Desarrollo

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python panel_inventario.py                 # app de escritorio (ventana pywebview)
.venv/bin/python panel_inventario.py --selftest      # comprobación sin ventana
DEMO_MODE=1 DEMO_SOURCE=ficticio .venv/bin/uvicorn app.main:app --port 8765   # panel en el navegador con datos ficticios
.venv/bin/python -m scripts.exportar_estatico        # demo estática en docs/
```

**Compilación:** [`.github/workflows/build-macos.yml`](.github/workflows/build-macos.yml) empaqueta la app con
PyInstaller ([`PanelInventario.spec`](PanelInventario.spec)) en un runner de macOS arm64, aplica una firma ad-hoc,
ejecuta el selftest y genera el `.zip` y el `.dmg`. Al subir una etiqueta `vX.Y.Z` que coincida con `APP_VERSION`
(en `app/config.py`), publica la release.

```
app/desktop.py          arranque: carpetas de macOS, servidor local con token y ventana nativa
app/main.py             API (FastAPI)
app/credentials.py      conexión con Odoo y con la IA; claves en el Llavero
app/odoo_client.py      cliente XML-RPC de solo lectura, con tiempo máximo de espera
app/holded.py           cliente de la API v2 de Holded (solo GET) y traducción al modelo de datos de la app
app/data.py             extracción de Odoo (y datos de demo) con caché
app/analytics.py        previsión, métricas, ABC/XYZ y KPIs
app/alerts.py           reglas de alerta, alertas vistas, resúmenes y uso (SQLite)
app/ai.py               asistente multiproveedor con herramientas de solo lectura
app/models_catalog.py   catálogo de modelos y precios; validación de claves
static/                 interfaz (HTML/CSS/JS, Chart.js local)
scripts/instalar-mac.sh instalador y actualizador para macOS
```

## Licencia

Copyright (C) 2026 Thothalpha

Este programa es software libre: puedes redistribuirlo y modificarlo bajo los términos de la **GNU Affero General
Public License versión 3** publicada por la Free Software Foundation. Se distribuye con la esperanza de que sea útil,
pero **sin ninguna garantía**. Consulta el archivo [LICENSE](LICENSE) para más detalles.
