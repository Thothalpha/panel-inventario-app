"""Documentos del asistente: importar (Excel, CSV, PDF) y generar (Excel, PDF).

- Cada sección (Odoo / Holded) tiene su carpeta: DATA_DIR/<sección>/files/ con un índice index.json.
- Los archivos se identifican por id; nunca se aceptan rutas del usuario ni del modelo.
- Para descargar, la app copia el archivo a ~/Downloads (en la app de escritorio un enlace normal no sirve).
"""

import csv
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from copy import copy
from datetime import date, datetime
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

MAX_UPLOAD = 25 * 1024 * 1024
KEEP_DAYS = 60
TYPES = {".xlsx": "excel", ".xlsm": "excel", ".csv": "csv", ".pdf": "pdf"}
MIME = {"excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv",
        "pdf": "application/pdf"}


class FileError(ValueError):
    pass


# ----------------------------------------------------------------------------- índice
def _dir() -> Path:
    d = config.section_dir() / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return _dir() / "index.json"


def _load() -> list:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []


def _save(items: list):
    tmp = _index_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_index_path())


def list_files() -> list:
    _cleanup()
    return sorted(_load(), key=lambda f: f["created"], reverse=True)


def get(fid: str) -> dict:
    f = next((x for x in _load() if x["id"] == fid), None)
    if not f:
        raise FileError(f"No existe el documento {fid}")
    return f


def path_of(f: dict) -> Path:
    return _dir() / f["stored"]


def safe_name(name: str, ext: str) -> str:
    base = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", Path(str(name or "documento")).stem).strip(" .") or "documento"
    return base[:80] + ext


def _register(kind: str, name: str, src_bytes: bytes = None, src_path: Path = None, origin: str = "") -> dict:
    ext = Path(name).suffix.lower()
    fid = uuid.uuid4().hex[:12]
    stored = f"{fid}{ext}"
    dest = _dir() / stored
    if src_bytes is not None:
        dest.write_bytes(src_bytes)
    else:
        shutil.move(str(src_path), dest)
    item = {"id": fid, "name": name, "kind": kind, "type": TYPES.get(ext, "otro"), "size": dest.stat().st_size,
            "created": datetime.now().isoformat(timespec="seconds"), "stored": stored, "origin": origin}
    try:
        item["summary"] = describe(item)
    except Exception as e:  # un archivo dañado no impide guardarlo; el error se verá al leerlo
        item["summary"] = f"No se ha podido leer: {e}"
    items = _load()
    items.append(item)
    _save(items)
    return item


def delete(fid: str):
    items = _load()
    f = next((x for x in items if x["id"] == fid), None)
    if not f:
        raise FileError(f"No existe el documento {fid}")
    try:
        path_of(f).unlink()
    except FileNotFoundError:
        pass
    _save([x for x in items if x["id"] != fid])


def _cleanup():
    """Borra los documentos de más de KEEP_DAYS días (siguen en Descargas si se guardaron)."""
    items, keep, limit = _load(), [], time.time() - KEEP_DAYS * 86400
    for f in items:
        try:
            if datetime.fromisoformat(f["created"]).timestamp() < limit:
                path_of(f).unlink(missing_ok=True)
                continue
        except ValueError:
            pass
        keep.append(f)
    if len(keep) != len(items):
        _save(keep)


# ----------------------------------------------------------------------------- importar
def import_file(name: str, content: bytes) -> dict:
    ext = Path(name or "").suffix.lower()
    if ext == ".xls":
        raise FileError("Los .xls antiguos no se pueden leer: guárdalo como .xlsx desde Excel")
    if ext not in TYPES:
        raise FileError("Formato no admitido: sube un Excel (.xlsx), un CSV o un PDF")
    if not content:
        raise FileError("El archivo está vacío")
    if len(content) > MAX_UPLOAD:
        raise FileError(f"El archivo supera {MAX_UPLOAD // 1024 // 1024} MB")
    return _register("importado", safe_name(name, ext), src_bytes=content)


def describe(f: dict) -> str:
    p = path_of(f)
    if f["type"] == "excel":
        import openpyxl
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        try:
            return "Hojas: " + "; ".join(f"{ws.title} ({ws.max_row or 0} filas × {ws.max_column or 0} columnas)"
                                         for ws in wb.worksheets)
        finally:
            wb.close()
    if f["type"] == "pdf":
        from pypdf import PdfReader
        return f"{len(PdfReader(p).pages)} páginas"
    if f["type"] == "csv":
        return f"{len(_csv_rows(p))} filas"
    return ""


def _csv_rows(p: Path) -> list:
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:5000], delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
    return list(csv.reader(io.StringIO(text), dialect))


def _cell(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def read(fid: str, sheet: str = None, start: int = 1, rows: int = 150) -> dict:
    """Contenido de un documento para el asistente (Excel con coordenadas de celda, para poder rellenarlo)."""
    f = get(fid)
    p = path_of(f)
    rows = max(1, min(int(rows or 150), 400))
    start = max(1, int(start or 1))
    if f["type"] == "excel":
        import openpyxl
        from openpyxl.utils import get_column_letter
        wb = openpyxl.load_workbook(p, data_only=True)
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
        out = []
        end = min(ws.max_row, start + rows - 1)
        for r in range(start, end + 1):
            cells = {}
            for c in range(1, min(ws.max_column, 60) + 1):
                v = ws.cell(r, c).value
                if v not in (None, ""):
                    cells[get_column_letter(c)] = _cell(v)
            if cells:
                out.append({"fila": r, "celdas": cells})
        return {"documento": f["name"], "hojas": wb.sheetnames, "hoja": ws.title,
                "dimensiones": ws.dimensions, "filas_totales": ws.max_row,
                "celdas_combinadas": [str(m) for m in list(ws.merged_cells.ranges)[:40]],
                "desde_fila": start, "hasta_fila": end, "filas": out,
                "nota": ("Hay más filas: pide desde_fila=" + str(end + 1)) if end < ws.max_row else "Fin de la hoja"}
    if f["type"] == "csv":
        allr = _csv_rows(p)
        part = allr[start - 1:start - 1 + rows]
        return {"documento": f["name"], "filas_totales": len(allr), "desde_fila": start,
                "filas": [{"fila": start + i, "valores": r} for i, r in enumerate(part)]}
    if f["type"] == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(p)
        n = len(reader.pages)
        first, last = start, min(n, start + min(rows, 15) - 1)
        pages = []
        for i in range(first - 1, last):
            txt = (reader.pages[i].extract_text() or "").strip()
            pages.append({"pagina": i + 1, "texto": txt[:12000]})
        return {"documento": f["name"], "paginas_totales": n, "paginas": pages,
                "nota": ("Hay más páginas: pide desde_fila=" + str(last + 1) + " (= página)") if last < n else "Fin del PDF"}
    raise FileError("Tipo de documento no legible")


# ----------------------------------------------------------------------------- generar Excel
def _xl_styles():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    thin = Side(style="thin", color="FFD0D0D0")
    return {"head_font": Font(bold=True, color="FFFFFFFF"), "head_fill": PatternFill("solid", fgColor="FF1F3A5F"),
            "border": Border(bottom=thin), "title": Font(bold=True, size=14), "sub": Font(italic=True, size=10, color="FF595959"),
            "wrap": Alignment(vertical="top", wrap_text=False), "center": Alignment(horizontal="center")}


def _num(v):
    """Convierte cadenas numéricas a número para que Excel pueda sumar (respeta textos como referencias)."""
    if isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?|-?\d+(,\d+)?", s):
            try:
                return float(s.replace(".", "").replace(",", ".")) if "," in s or re.search(r"\.\d{3}", s) else int(s)
            except ValueError:
                return v
        if re.fullmatch(r"-?\d+\.\d+", s):
            return float(s)
    return v


def _write_sheet(ws, columns, rows, title=None, subtitle=None, num_formats=None):
    from openpyxl.utils import get_column_letter
    st = _xl_styles()
    r0 = 1
    if title:
        ws.cell(1, 1, title).font = st["title"]
        if subtitle:
            ws.cell(2, 1, subtitle).font = st["sub"]
        r0 = 4
    for j, c in enumerate(columns, 1):
        cell = ws.cell(r0, j, str(c))
        cell.font, cell.fill = st["head_font"], st["head_fill"]
    widths = [len(str(c)) for c in columns]
    for i, row in enumerate(rows, 1):
        for j, v in enumerate(list(row)[:len(columns)], 1):
            v = _num(v)
            cell = ws.cell(r0 + i, j, v)
            fmt = (num_formats or {}).get(columns[j - 1])
            if fmt and isinstance(v, (int, float)):
                cell.number_format = fmt
            widths[j - 1] = max(widths[j - 1], min(len(str(v)) if v is not None else 0, 60))
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = max(8, min(w + 2, 62))
    ws.freeze_panes = ws.cell(r0 + 1, 1)
    if rows:
        ws.auto_filter.ref = f"A{r0}:{get_column_letter(len(columns))}{r0 + len(rows)}"


def _finish(wb, name: str, origin: str) -> dict:
    tmp = _dir() / f"tmp-{uuid.uuid4().hex}.xlsx"
    wb.save(tmp)
    return _register("generado", safe_name(name, ".xlsx"), src_path=tmp, origin=origin)


def create_excel(name: str, sheets: list, origin="asistente") -> dict:
    import openpyxl
    if not sheets:
        raise FileError("El Excel necesita al menos una hoja")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    used = set()
    for i, sh in enumerate(sheets[:20]):
        cols = [str(c) for c in (sh.get("columnas") or [])]
        if not cols:
            raise FileError(f"La hoja {i + 1} no tiene columnas")
        title = re.sub(r"[\[\]:*?/\\]", " ", str(sh.get("nombre") or f"Hoja {i + 1}"))[:31] or f"Hoja {i + 1}"
        while title in used:
            title = (title[:28] + f" {i + 1}")[:31]
        used.add(title)
        ws = wb.create_sheet(title)
        rows = sh.get("filas") or []
        if len(rows) > 20000:
            raise FileError("Demasiadas filas (máximo 20.000 por hoja)")
        _write_sheet(ws, cols, rows, sh.get("titulo"), sh.get("subtitulo"))
    return _finish(wb, name, origin)


def fill_excel(fid: str, changes: list, name: str = None, style_row: int = None) -> dict:
    """Copia un Excel importado y escribe valores en celdas concretas, conservando el formato de la plantilla."""
    import openpyxl
    from openpyxl.utils.cell import coordinate_from_string
    f = get(fid)
    if f["type"] != "excel":
        raise FileError("Solo se pueden rellenar documentos Excel (.xlsx)")
    if not changes:
        raise FileError("No hay celdas que rellenar")
    if len(changes) > 50000:
        raise FileError("Demasiados cambios en una sola llamada (máximo 50.000)")
    wb = openpyxl.load_workbook(path_of(f))
    for ch in changes:
        sheet = ch.get("hoja")
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
        coord = str(ch.get("celda") or "").upper().strip()
        try:
            col, row = coordinate_from_string(coord)
        except Exception:
            raise FileError(f"Celda no válida: {coord!r}")
        cell = ws[coord]
        if type(cell).__name__ == "MergedCell":
            raise FileError(f"{coord} forma parte de una celda combinada: escribe en la celda superior izquierda")
        cell.value = _num(ch.get("valor"))
        if style_row and row != style_row and not cell.has_style:
            src = ws[f"{col}{style_row}"]
            if src.has_style:
                cell._style = copy(src._style)
    stem = Path(f["name"]).stem
    return _finish(wb, name or f"{stem} (relleno)", origin=f"plantilla:{f['name']}")


# Campos de producto que se pueden exportar desde el panel (clave de métricas → título de columna)
PRODUCT_FIELDS = {
    "ref": "Referencia", "name": "Producto", "categ": "Categoría", "family": "Familia / colección", "uom": "Unidad",
    "on_hand": "Stock a mano", "free": "Stock libre", "virtual": "Stock previsto", "incoming": "Entradas previstas",
    "outgoing": "Salidas previstas", "cost": "Coste unitario", "price": "Precio venta", "value": "Valor stock (€)",
    "demand_4w": "Demanda 4 sem", "demand_13w": "Demanda 13 sem", "demand_52w": "Demanda 52 sem",
    "forecast_30d": "Previsión 30 d", "forecast_90d": "Previsión 90 d", "coverage_days": "Cobertura (días)",
    "stockout_date": "Rotura estimada", "lead_time_days": "Plazo entrega (d)", "supplier": "Proveedor",
    "safety_stock": "Stock seguridad", "reorder_point": "Punto de pedido", "suggested_order": "Cantidad sugerida",
    "orderpoint_min": "Mínimo ERP", "orderpoint_max": "Máximo ERP", "pending_po_qty": "Compras pendientes",
    "abc": "ABC", "xyz": "XYZ", "days_idle": "Días sin movimiento", "last_move": "Último movimiento",
    "revenue_52w": "Facturación 12 m", "returns_52w": "Devoluciones 12 m", "sizes_out": "Tallas agotadas",
}
_MONEY = {"Coste unitario", "Precio venta", "Valor stock (€)", "Facturación 12 m"}
SIZE_COLS = ["XS", "S", "M", "L", "XL", "XXL"]


def _select_products(metrics: dict, refs=None, text=None, category=None, family=None, only_stock=False):
    """Devuelve (productos, referencias_no_encontradas)."""
    rows, missing = list(metrics.values()), []
    if refs:
        wanted = [str(r).strip().lower() for r in refs if str(r).strip()]
        by_ref = {r["ref"].lower(): r for r in rows if r.get("ref")}
        found, missing = [], []
        for w in wanted:
            if w in by_ref:
                found.append(by_ref[w])
            else:
                hits = [r for r in rows if w in (r.get("ref") or "").lower() or w in (r.get("name") or "").lower()]
                (found.extend(hits) if hits else missing.append(w))
        rows = list({id(r): r for r in found}.values())
    if text:
        t = text.lower()
        rows = [r for r in rows if t in (r.get("ref") or "").lower() or t in (r.get("name") or "").lower()
                or t in (r.get("categ") or "").lower() or t in (r.get("family") or "").lower()]
    if category:
        rows = [r for r in rows if category.lower() in (r.get("categ") or "").lower()]
    if family:
        rows = [r for r in rows if family.lower() == (r.get("family") or "").lower()]
    if only_stock:
        rows = [r for r in rows if (r.get("on_hand") or 0) > 0]
    return rows, missing


def export_products(metrics: dict, source: str, fields: list, name: str, refs=None, text=None, category=None,
                    family=None, by_size="no", only_stock=False, title=None, order=None) -> dict:
    """Excel de productos con cifras tomadas directamente del panel (sin transcribir por la IA)."""
    import openpyxl
    fields = [f for f in (fields or []) if f in PRODUCT_FIELDS] or ["ref", "name", "on_hand", "forecast_30d", "coverage_days"]
    rows, missing = _select_products(metrics, refs, text, category, family, only_stock)
    if not rows:
        raise FileError("Ningún producto coincide con la selección" + (f" (no encontrados: {', '.join(missing)})" if missing else ""))
    if order and order.lstrip("-") in PRODUCT_FIELDS:
        key = order.lstrip("-")
        rows.sort(key=lambda r: (r.get(key) is None, r.get(key) if not isinstance(r.get(key), list) else len(r.get(key))),
                  reverse=order.startswith("-"))
    elif refs:
        pos = {str(x).strip().lower(): i for i, x in enumerate(refs)}
        rows.sort(key=lambda r: pos.get((r.get("ref") or "").lower(), 10 ** 6))
    else:
        rows.sort(key=lambda r: ((r.get("categ") or ""), (r.get("ref") or "")))

    def val(r, k):
        v = r.get(k)
        if isinstance(v, list):
            return ", ".join(map(str, v))
        if isinstance(v, float):
            return round(v, 2)
        return v

    cols = [PRODUCT_FIELDS[f] for f in fields]
    sheets_rows = []
    has_sizes = any(r.get("sizes") for r in rows)
    if by_size == "columnas" and has_sizes:
        cols = cols + [f"Talla {s}" for s in SIZE_COLS] + ["Otras tallas"]
        for r in rows:
            by = {}
            for z in r.get("sizes") or []:
                by[z["size"]] = by.get(z["size"], 0) + max(z["on_hand"], 0)
            extra = {k: v for k, v in by.items() if k not in SIZE_COLS}
            sheets_rows.append([val(r, k) for k in fields] + [by.get(s, "") for s in SIZE_COLS] +
                               [", ".join(f"{k}: {v:g}" for k, v in extra.items())])
    elif by_size == "filas" and has_sizes:
        cols = cols + ["Talla", "SKU talla", "Stock talla", "Vendidas 13 sem", "Previsión 30 d talla", "Cobertura talla (d)", "Estado talla"]
        for r in rows:
            for z in r.get("sizes") or [{}]:
                sheets_rows.append([val(r, k) for k in fields] + [z.get("size"), z.get("sku"), z.get("on_hand"),
                                   z.get("demand_13w"), z.get("forecast_30d"), z.get("coverage_days"), z.get("status")])
    else:
        sheets_rows = [[val(r, k) for k in fields] for r in rows]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Productos"
    fmts = {c: '#,##0.00 "€"' for c in cols if c in _MONEY}
    _write_sheet(ws, cols, sheets_rows, title or Path(name).stem,
                 f"Datos de {source} · generado el {datetime.now():%d/%m/%Y %H:%M}", fmts)
    item = _finish(wb, name, origin="panel")
    item = {**item, "filas": len(sheets_rows), "productos": len(rows)}
    if missing:
        item["no_encontrados"] = missing
    return item


# ----------------------------------------------------------------------------- generar PDF
_FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def _pdf_font(pdf) -> str:
    for p in _FONT_PATHS:
        if os.path.exists(p):
            pdf.add_font("Base", "", p)
            bold = p.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf").replace("Arial.ttf", "Arial Bold.ttf")
            pdf.add_font("Base", "B", bold if bold != p and os.path.exists(bold) else p)
            return "Base"
    return ""


def create_pdf(name: str, title: str, blocks: list, origin="asistente") -> dict:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos
    wide = any(len(b.get("columnas") or []) > 6 for b in blocks or [])
    pdf = FPDF(orientation="L" if wide else "P", format="A4")
    pdf.set_auto_page_break(True, margin=15)
    pdf.set_margins(15, 15, 15)
    family = _pdf_font(pdf)
    unicode_ok = bool(family)
    family = family or "Helvetica"

    def t(s):
        s = "" if s is None else str(s)
        if unicode_ok:
            return s
        return s.replace("€", "EUR").replace("—", "-").replace("–", "-").replace("·", "-").encode("latin-1", "replace").decode("latin-1")

    pdf.add_page()
    pdf.set_font(family, "B", 16)
    pdf.multi_cell(0, 8, t(title or Path(name).stem), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(family, "", 9)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 6, t(f"Generado el {datetime.now():%d/%m/%Y %H:%M}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)
    for b in blocks or []:
        kind = b.get("tipo") or "texto"
        if kind == "titulo":
            pdf.ln(2)
            pdf.set_font(family, "B", 13)
            pdf.multi_cell(0, 7, t(b.get("texto")), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        elif kind == "lista":
            pdf.set_font(family, "", 10)
            for it in b.get("elementos") or []:
                pdf.multi_cell(0, 5.5, t("•  " + str(it)), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        elif kind == "tabla":
            cols = [t(c) for c in b.get("columnas") or []]
            rows = b.get("filas") or []
            if not cols:
                continue
            pdf.set_font(family, "", 8 if len(cols) > 6 else 9)
            from fpdf.fonts import FontFace
            head = FontFace(emphasis="BOLD", fill_color=(225, 230, 238))
            with pdf.table(headings_style=head, line_height=5.5, text_align="LEFT", first_row_as_headings=True) as table:
                hr = table.row()
                for c in cols:
                    hr.cell(c)
                for r in rows[:3000]:
                    row = table.row()
                    for j in range(len(cols)):
                        row.cell(t(r[j] if j < len(r) else ""))
            pdf.ln(2)
        else:
            pdf.set_font(family, "", 10)
            pdf.multi_cell(0, 5.5, t(b.get("texto")), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1.5)
    tmp = _dir() / f"tmp-{uuid.uuid4().hex}.pdf"
    pdf.output(str(tmp))
    return _register("generado", safe_name(name, ".pdf"), src_path=tmp, origin=origin)


# ----------------------------------------------------------------------------- guardar / abrir
def downloads_dir() -> Path:
    d = Path.home() / "Downloads"
    d.mkdir(exist_ok=True)
    return d


def save_to_downloads(fid: str) -> str:
    f = get(fid)
    dest = downloads_dir() / f["name"]
    stem, ext, n = dest.stem, dest.suffix, 2
    while dest.exists():
        dest = dest.with_name(f"{stem} ({n}){ext}")
        n += 1
    shutil.copy2(path_of(f), dest)
    return str(dest)


def _launch(args):
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise FileError(f"No se ha podido abrir: {e}")


def open_file(path: str, reveal=False):
    """Abre un archivo guardado en Descargas (o lo muestra en el Finder). Solo rutas dentro de Descargas."""
    p = Path(path).resolve()
    if downloads_dir().resolve() not in p.parents or not p.exists():
        raise FileError("Solo se pueden abrir archivos guardados en Descargas")
    if sys.platform == "darwin":
        _launch(["open", "-R", str(p)] if reveal else ["open", str(p)])
    elif sys.platform.startswith("linux"):
        _launch(["xdg-open", str(p.parent if reveal else p)])
    elif os.name == "nt":
        os.startfile(str(p.parent if reveal else p))  # noqa: S606
