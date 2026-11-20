"""Skills del asistente: guías de trabajo en Markdown que el modelo carga cuando la tarea lo pide.

Formato de cada skill (archivo .md):

    ---
    name: excel-stock
    description: Cuándo usarla, en una frase (la ve el asistente para decidir si la carga)
    secciones: odoo, holded        # opcional; por defecto, todas
    ---
    Instrucciones…

- Integradas: carpeta skills/ de la app (llegan con cada actualización).
- Añadidas por el usuario: DATA_DIR/skills/ (se importan desde Configuración).
"""

import re
from pathlib import Path

from . import config

BUILTIN_DIR = config.ROOT / "skills"
USER_DIR = config.DATA_DIR / "skills"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
MAX_SIZE = 60_000


class SkillError(ValueError):
    pass


def parse(text: str) -> dict:
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        raise SkillError("Falta la cabecera (--- name / description ---) al principio del archivo")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.split(" #")[0].strip().strip('"').strip("'")
    name = meta.get("name", "").lower()
    if not NAME_RE.match(name):
        raise SkillError("name debe ser corto, en minúsculas y con guiones (p. ej. excel-stock)")
    if not meta.get("description"):
        raise SkillError("Falta description: una frase que diga cuándo usar la skill")
    secs = [s.strip().lower() for s in meta.get("secciones", "").split(",") if s.strip()]
    return {"name": name, "description": meta["description"][:400], "sections": secs or list(config.SECTIONS),
            "body": m.group(2).strip()}


def _read_dir(d: Path, origin: str) -> dict:
    out = {}
    if d.is_dir():
        for p in sorted(d.glob("*.md")):
            try:
                sk = parse(p.read_text(encoding="utf-8"))
                out[sk["name"]] = {**sk, "origin": origin, "file": p.name}
            except (SkillError, OSError, UnicodeDecodeError):
                continue
    return out


def all_skills() -> dict:
    """Todas las skills (las del usuario sustituyen a una integrada con el mismo nombre)."""
    skills = {**_read_dir(BUILTIN_DIR, "integrada"), **_read_dir(USER_DIR, "añadida")}
    disabled = set(config.load_app_state().get("skills_desactivadas") or [])
    for s in skills.values():
        s["enabled"] = s["name"] not in disabled
    return skills


def available(section: str = None) -> list:
    sec = section or config.section()
    return [s for s in all_skills().values() if s["enabled"] and sec in s["sections"]]


def public_list() -> list:
    return [{k: s[k] for k in ("name", "description", "sections", "origin", "enabled")}
            for s in sorted(all_skills().values(), key=lambda x: (x["origin"], x["name"]))]


def load(name: str) -> dict:
    s = next((x for x in available() if x["name"] == str(name).strip().lower()), None)
    if not s:
        raise SkillError(f"No hay ninguna skill activa llamada «{name}»")
    return {"skill": s["name"], "instrucciones": s["body"].replace("{ERP}", config.erp_name())}


def install(filename: str, content: bytes) -> dict:
    if len(content) > MAX_SIZE:
        raise SkillError("La skill es demasiado grande (máx. 60 KB)")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise SkillError("La skill debe ser un archivo de texto UTF-8 (.md)")
    sk = parse(text)
    USER_DIR.mkdir(parents=True, exist_ok=True)
    (USER_DIR / f"{sk['name']}.md").write_text(text, encoding="utf-8")
    set_enabled(sk["name"], True)
    return {k: sk[k] for k in ("name", "description", "sections")}


def remove(name: str):
    p = USER_DIR / f"{name}.md"
    if not NAME_RE.match(name) or not p.exists():
        raise SkillError("Solo se pueden eliminar las skills añadidas por ti")
    p.unlink()


def set_enabled(name: str, enabled: bool):
    if name not in all_skills():
        raise SkillError(f"No existe la skill «{name}»")
    disabled = set(config.load_app_state().get("skills_desactivadas") or [])
    (disabled.discard if enabled else disabled.add)(name)
    config.save_app_state(skills_desactivadas=sorted(disabled))


def prompt_section() -> str:
    sk = available()
    if not sk:
        return ""
    lines = "\n".join(f"  · {s['name']}: {s['description']}" for s in sk)
    return ("\n\nSkills disponibles (guías de trabajo). Si la petición encaja con una, llama a cargar_skill con su nombre "
            "ANTES de empezar y sigue sus instrucciones:\n" + lines)
