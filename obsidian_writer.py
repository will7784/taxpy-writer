"""
Obsidian Vault Writer — ImpuestIA

Escribe archivos .md con frontmatter YAML y [[wikilinks]] en el vault de Obsidian.
Soporta múltiples tipos de contenido: resumenes, jurisprudencia, peticiones,
analisis, notas, y estructura por cliente.
"""

from __future__ import annotations

import re
import yaml
from datetime import datetime
from pathlib import Path
from typing import Optional

import config

VAULT = config.OBSIDIAN_VAULT_PATH
COWORK = config.COWORK_PATH


def _sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "-", name).strip()


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _format_tags(tags: list[str] | str | None) -> str:
    if not tags:
        return ""
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return " [" + ", ".join(tags) + "]"


def _build_frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if v is None or v == "":
            continue
        if isinstance(v, list):
            lines.append(f"{k}: {yaml.dump(v, allow_unicode=True, default_flow_style=True).strip()}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        else:
            val = str(v).replace("\n", " ").replace('"', "'")
            lines.append(f'{k}: "{val}"')
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _wikilink(text: str) -> str:
    return f"[[{text}]]"


def write_note(
    folder: str,
    filename: str,
    content: str,
    *,
    title: Optional[str] = None,
    tipo: str = "nota",
    cliente: Optional[str] = None,
    tags: Optional[list[str]] = None,
    fuentes: Optional[list[str]] = None,
    articulo: Optional[str] = None,
    extra_meta: Optional[dict] = None,
) -> Path:
    """
    Escribe una nota Markdown con frontmatter en el vault.

    Args:
        folder: subcarpeta relativa al vault (ej: "Leyes", "Clientes/Nano_Calderon/Analisis")
        filename: nombre del archivo sin extension
        content: contenido Markdown del cuerpo
        title: titulo de la nota (si no, se usa filename)
        tipo: tipo de contenido (resumen_legal, jurisprudencia, peticion, analisis, nota, skill)
        cliente: nombre del cliente si aplica
        tags: lista de tags para frontmatter
        fuentes: fuentes legales citadas
        articulo: articulo de ley relacionado
        extra_meta: dict con metadatos adicionales
    """
    safe_name = _sanitize_filename(filename)
    target_dir = _ensure_dir(VAULT / folder)
    filepath = target_dir / f"{safe_name}.md"

    meta = {
        "fecha": datetime.now().strftime("%Y-%m-%d"),
        "tipo": tipo,
    }
    if title:
        meta["title"] = title
    if cliente:
        meta["cliente"] = cliente
    if articulo:
        meta["articulo"] = articulo
    if fuentes:
        meta["fuentes"] = fuentes
    if tags:
        meta["tags"] = tags
    if extra_meta:
        meta.update(extra_meta)

    display_title = title or safe_name.replace("_", " ")
    body = _build_frontmatter(meta)
    body += f"# {display_title}\n\n"
    body += content
    if not body.endswith("\n"):
        body += "\n"

    filepath.write_text(body, encoding="utf-8")
    return filepath


def write_resumen_legal(
    ley: str,
    articulo: str,
    content: str,
    *,
    title: Optional[str] = None,
    tags: Optional[list[str]] = None,
    fuentes: Optional[list[str]] = None,
) -> Path:
    """Escribe un resumen legal en Leyes/ o Articulos/."""
    safe_ley = _sanitize_filename(ley)
    safe_art = _sanitize_filename(articulo)
    folder = f"Leyes/{safe_ley}"
    filename = safe_art.replace(" ", "_")
    return write_note(
        folder=folder,
        filename=filename,
        content=content,
        title=title or f"{ley} — {articulo}",
        tipo="resumen_legal",
        articulo=articulo,
        tags=tags or [safe_ley.lower(), safe_art.lower().replace(" ", "-")],
        fuentes=fuentes,
    )


def write_jurisprudencia(
    content: str,
    *,
    cliente: Optional[str] = None,
    filename: Optional[str] = None,
    tags: Optional[list[str]] = None,
    fuente: Optional[str] = None,
    organismo: str = "SII",
) -> Path:
    """Escribe jurisprudencia en el vault."""
    if cliente:
        folder = f"Clientes/{_sanitize_filename(cliente)}/Jurisprudencia"
    else:
        folder = "Jurisprudencia"

    safe_name = filename or f"fallo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return write_note(
        folder=folder,
        filename=safe_name,
        content=content,
        tipo="jurisprudencia",
        cliente=cliente,
        tags=tags,
        fuentes=[fuente] if fuente else None,
    )


def write_peticion(
    cliente: str,
    content: str,
    *,
    filename: Optional[str] = None,
    titulo: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> Path:
    """Escribe una peticion administrativa en la carpeta del cliente."""
    safe_cliente = _sanitize_filename(cliente)
    safe_name = filename or f"peticion_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return write_note(
        folder=f"Clientes/{safe_cliente}/Peticiones",
        filename=safe_name,
        content=content,
        title=titulo or safe_name.replace("_", " "),
        tipo="peticion",
        cliente=cliente,
        tags=tags,
    )


def write_analisis(
    cliente: str,
    content: str,
    *,
    filename: Optional[str] = None,
    titulo: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> Path:
    """Escribe un analisis de caso en la carpeta del cliente."""
    safe_cliente = _sanitize_filename(cliente)
    safe_name = filename or f"analisis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return write_note(
        folder=f"Clientes/{safe_cliente}/Analisis",
        filename=safe_name,
        content=content,
        title=titulo or safe_name.replace("_", " "),
        tipo="analisis",
        cliente=cliente,
        tags=tags,
    )


def write_skill_note(
    skill_name: str,
    content: str,
    *,
    triggers: Optional[list[str]] = None,
    description: Optional[str] = None,
) -> Path:
    """Exporta un skill como nota de referencia en el vault."""
    safe_name = _sanitize_filename(skill_name)
    return write_note(
        folder="Skills",
        filename=safe_name,
        content=content,
        title=f"Skill: {skill_name}",
        tipo="skill",
        tags=triggers or [],
        extra_meta={"description": description} if description else None,
    )


def init_vault_structure() -> dict[str, Path]:
    """Crea la estructura inicial del vault si no existe."""
    dirs = {
        "indice": _ensure_dir(VAULT / "00_Indice"),
        "leyes": _ensure_dir(VAULT / "Leyes"),
        "articulos": _ensure_dir(VAULT / "Articulos"),
        "jurisprudencia": _ensure_dir(VAULT / "Jurisprudencia"),
        "clientes": _ensure_dir(VAULT / "Clientes"),
        "templates": _ensure_dir(VAULT / "Templates"),
        "skills": _ensure_dir(VAULT / "Skills"),
    }
    return dirs


def init_cliente_structure(nombre: str, rut: str = "", rubro: str = "", regimen: str = "", contacto: str = "", notas: str = "") -> dict[str, Path]:
    """
    Crea la estructura de carpetas para un cliente y su .cliente.yaml.
    """
    safe_name = _sanitize_filename(nombre)
    base = COWORK / safe_name

    dirs = {
        "root": _ensure_dir(base),
        "entrada": _ensure_dir(base / "entrada"),
        "procesados": _ensure_dir(base / "procesados"),
        "peticiones": _ensure_dir(base / "Peticiones"),
        "analisis": _ensure_dir(base / "Analisis"),
        "jurisprudencia": _ensure_dir(base / "Jurisprudencia"),
        "notas": _ensure_dir(base / "Notas"),
        "documentacion": _ensure_dir(base / "Documentacion"),
    }

    yaml_path = base / ".cliente.yaml"
    yaml_content = {
        "nombre": nombre,
        "rut": rut,
        "rubro": rubro,
        "regimen": regimen,
        "contacto": contacto,
        "notas": notas,
        "creado": datetime.now().strftime("%Y-%m-%d"),
    }
    yaml_path.write_text(
        yaml.dump(yaml_content, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )

    return dirs


def list_clientes() -> list[dict]:
    """Lista todos los clientes configurados."""
    if not COWORK.exists():
        return []
    clientes = []
    for d in sorted(COWORK.iterdir()):
        if d.is_dir():
            yaml_path = d / ".cliente.yaml"
            info = {"nombre": d.name}
            if yaml_path.exists():
                try:
                    info.update(yaml.safe_load(yaml_path.read_text(encoding="utf-8")))
                except Exception:
                    pass
            clientes.append(info)
    return clientes


def get_cliente_dir(nombre: str) -> Path:
    """Retorna el directorio base de un cliente."""
    return COWORK / _sanitize_filename(nombre)


def list_cliente_files(nombre: str, subfolder: str = "") -> list[Path]:
    """Lista archivos en una carpeta de cliente."""
    base = get_cliente_dir(nombre)
    target = base / subfolder if subfolder else base
    if not target.exists():
        return []
    return sorted([p for p in target.iterdir() if p.is_file() and not p.name.startswith(".")])
