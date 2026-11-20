"""Novedades de cada versión: se muestran una sola vez, en la primera apertura tras actualizar."""

from . import config

# De la más reciente a la más antigua. Cada punto, una frase pensada para quien usa la app (no para programadores).
NOVEDADES = [
    {"version": "0.5.0", "fecha": "2026-11-20", "titulo": "Documentos, Excel y skills en el asistente", "puntos": [
        "Adjunta Excel, CSV o PDF en el chat (clip 📎 junto al cuadro de texto): el asistente los lee y trabaja con ellos.",
        "Pide al asistente un Excel o un PDF, por ejemplo «hazme un Excel con el stock disponible de estas referencias, "
        "con nombre, stock y previsión a 30 días»: lo monta con los datos del panel y lo guardas en Descargas con un clic.",
        "También rellena tus plantillas: adjunta el Excel y dile qué poner en cada columna.",
        "Nueva sección «Documentos» en el Asistente con todo lo que has subido o generado.",
        "El asistente carga «skills» (guías de trabajo) según la tarea. Las ves y gestionas en Configuración.",
        "Se ha quitado la guía de uso; a partir de ahora verás esta ventana con las novedades tras cada actualización.",
    ]},
    {"version": "0.4.1", "fecha": "2026-11-20", "titulo": "Nombres de producto como en Odoo", "puntos": [
        "Los nombres de producto salen en tu idioma de Odoo (antes algunos aparecían con la traducción inglesa).",
    ]},
    {"version": "0.4.0", "fecha": "2026-11-18", "titulo": "Holded: tallas, colecciones y canales", "puntos": [
        "Stock, ventas y alertas por talla; colecciones y tipos de prenda; ventas por canal y devoluciones.",
    ]},
]


def _ver(v: str):
    return tuple(int(x) for x in str(v).lstrip("v").split(".")[:3] if x.isdigit())


def pending() -> dict:
    """Novedades que el usuario aún no ha visto (vacío en una instalación nueva o si ya las vio)."""
    st = config.load_app_state()
    seen = st.get("novedades_vistas")
    current = config.APP_VERSION
    if not seen:
        if not config.existing_install():
            # Instalación nueva: no hay «novedades»; se marca para avisar a partir de la próxima actualización
            config.save_app_state(novedades_vistas=current)
            return {"show": False, "current": current, "versions": []}
        seen = "0"
    versions = [n for n in NOVEDADES if _ver(seen) < _ver(n["version"]) <= _ver(current)]
    if not seen or seen == "0":
        versions = versions[:1]  # actualizando desde una versión sin este aviso: solo lo de esta versión
    return {"show": bool(versions), "current": current, "versions": versions}


def mark_seen():
    config.save_app_state(novedades_vistas=config.APP_VERSION)
