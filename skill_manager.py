"""
Skill Manager — ImpuestIA

Gestiona skills dinamicas del agente tributario.
Cada skill vive en .agents/skills/{nombre}/SKILL.md con:
- YAML frontmatter: name, description, triggers, whenToUse
- Cuerpo Markdown: instrucciones para el LLM
- Subcarpetas opcionales: templates/, examples/

Flujo:
  1. Usuario (Telegram/Web) pasa una plantilla o ejemplo
  2. skill_manager analiza y genera SKILL.md automaticamente
  3. prompt_builder.py carga skills activos por keyword matching
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

import config

SKILLS_DIR = config.BASE_DIR / ".agents" / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "-", name.strip()).lower().strip("-")


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Extrae frontmatter YAML del contenido. Retorna (meta, body)."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].strip()


def _read_skill_md(name: str) -> Optional[str]:
    path = SKILLS_DIR / name / "SKILL.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def create_skill(
    name: str,
    description: str,
    *,
    triggers: Optional[list[str]] = None,
    when_to_use: str = "",
    instructions: str = "",
    templates: Optional[dict[str, str]] = None,
    examples: Optional[dict[str, str]] = None,
) -> Path:
    """
    Crea un nuevo skill dinamico.

    Args:
        name: nombre del skill (ej: 'peticion-sii')
        description: descripcion corta
        triggers: palabras clave que activan el skill
        when_to_use: descripcion de cuando usar este skill
        instructions: instrucciones detalladas para el LLM
        templates: dict {nombre_archivo: contenido_markdown} para plantillas
        examples: dict {nombre_archivo: contenido} para ejemplos
    """
    safe_name = _sanitize_name(name)
    skill_dir = SKILLS_DIR / safe_name
    skill_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = {
        "name": name,
        "description": description,
    }
    if triggers:
        frontmatter["triggers"] = triggers
    if when_to_use:
        frontmatter["whenToUse"] = when_to_use

    yaml_header = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False).strip()

    body_parts = [f"---\n{yaml_header}\n---\n"]
    body_parts.append(f"# Skill: {name}\n")
    body_parts.append(f"## Cuando usar\n{when_to_use or description}\n")

    if instructions:
        body_parts.append(f"## Instrucciones\n{instructions}\n")

    # Agregar referencia a templates
    template_dir = skill_dir / "templates"
    if templates:
        template_dir.mkdir(parents=True, exist_ok=True)
        body_parts.append("## Plantillas disponibles\n")
        for tpl_name, tpl_content in templates.items():
            tpl_path = template_dir / tpl_name
            tpl_path.write_text(tpl_content, encoding="utf-8")
            body_parts.append(f"- `{tpl_name}` — {len(tpl_content)} caracteres\n")

    # Agregar referencia a ejemplos
    examples_dir = skill_dir / "examples"
    if examples:
        examples_dir.mkdir(parents=True, exist_ok=True)
        body_parts.append("## Ejemplos\n")
        for ex_name, ex_content in examples.items():
            ex_path = examples_dir / ex_name
            ex_path.write_text(ex_content, encoding="utf-8")
            body_parts.append(f"- `{ex_name}` — {len(ex_content)} caracteres\n")

    # Escribir SKILL.md
    skill_md = skill_dir / "SKILL.md"
    full_content = "\n".join(body_parts)
    skill_md.write_text(full_content, encoding="utf-8")

    return skill_dir


def create_skill_from_template(
    name: str,
    description: str,
    template_path: str,
    *,
    triggers: Optional[list[str]] = None,
    when_to_use: str = "",
) -> Path:
    """
    Crea un skill a partir de un archivo de plantilla existente (.docx, .txt, .md).

    Extrae la estructura del documento para generar instrucciones de redaccion
    y guarda el template como ejemplo en el skill.
    """
    from pathlib import Path as _Path

    tp = _Path(template_path)
    if not tp.exists():
        raise FileNotFoundError(f"Plantilla no encontrada: {template_path}")

    if tp.suffix.lower() == ".docx":
        from docx import Document
        doc = Document(str(tp))
        content = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    else:
        content = tp.read_text(encoding="utf-8")

    sections = _extract_sections(content)
    instructions = _build_redaction_instructions(name, sections, when_to_use)

    return create_skill(
        name=name,
        description=description,
        triggers=triggers or _infer_triggers(name, content),
        when_to_use=when_to_use or f"Usar cuando el usuario necesite redactar una {name.replace('-', ' ')}",
        instructions=instructions,
        examples={tp.name: content},
    )


def _extract_sections(content: str) -> list[dict]:
    """Extrae secciones de un documento legal (patrones de encabezados)."""
    section_pattern = re.compile(
        r'^((?:\d+[\.\-)\s]+|[A-ZÁÉÍÓÚ][A-ZÁÉÍÓÚ\s]+[:]))\s*(.+)$',
        re.MULTILINE,
    )
    matches = list(section_pattern.finditer(content))
    if not matches:
        return [{"title": "Documento completo", "body": content}]

    sections = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections.append({
            "title": f"{m.group(1)} {m.group(2)}".strip(),
            "body": content[start:end].strip(),
        })
    return sections


def _build_redaction_instructions(skill_name: str, sections: list[dict], contexto: str = "") -> str:
    """Construye instrucciones de redaccion basadas en la estructura detectada."""
    lines = [
        f"Eres un redactor especializado en {skill_name.replace('-', ' ')}.",
        "",
        "Debes redactar el documento siguiendo esta estructura:",
        "",
    ]

    for i, sec in enumerate(sections, 1):
        lines.append(f"{i}. **{sec['title']}**")
        preview = sec["body"][:200].replace("\n", " ").strip()
        lines.append(f"   - Contenido esperado: {preview}...")
        lines.append("")

    if contexto:
        lines.append(f"Contexto adicional: {contexto}")
        lines.append("")

    lines.extend([
        "## Reglas de redaccion",
        "- Usa lenguaje formal pero claro, dirigido al Servicio de Impuestos Internos (SII).",
        "- Incluye fundamentos de derecho con citas exactas (articulo, ley, decreto).",
        "- Agrega una seccion de documentacion de respaldo al final.",
        "- Solicita al usuario los datos faltantes (RUT, nombre, direccion, etc.) antes de redactar.",
        "- Usa el formato de la plantilla de ejemplo como referencia exacta de tono y estilo.",
        "",
        "## Checklist de calidad",
        "- [ ] Todos los datos del contribuyente estan completos?",
        "- [ ] Cada afirmacion legal tiene su cita exacta?",
        "- [ ] La peticion concreta es clara y accionable por el SII?",
        "- [ ] Se menciona la documentacion de respaldo adjunta?",
    ])

    return "\n".join(lines)


def _infer_triggers(name: str, content: str) -> list[str]:
    """Infiere triggers basados en el nombre y contenido del skill."""
    triggers = [name.replace("-", " ")]

    keyword_map = {
        "peticion": ["peticion", "solicitud", "respuesta", "escrito", "presentacion", "reclamo"],
        "recurso": ["recurso", "reposicion", "apelacion", "reclamacion"],
        "observacion": ["observacion", "g113", "g22", "citacion", "fiscalizacion"],
        "sii": ["sii", "servicio de impuestos", "impuestos internos"],
        "renta": ["renta", "declaracion de renta", "f22", "formulario 22"],
        "iva": ["iva", "declaracion de iva", "f29", "formulario 29"],
    }

    search = (name + " " + content[:500]).lower()
    for key, words in keyword_map.items():
        if any(w in search for w in words):
            triggers.extend(words)

    return list(set(triggers))


def list_skills() -> list[dict]:
    """Lista todos los skills disponibles con sus metadatos."""
    if not SKILLS_DIR.exists():
        return []
    result = []
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.exists():
            continue
        raw = skill_md.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(raw)

        has_templates = (d / "templates").exists()
        has_examples = (d / "examples").exists()

        result.append({
            "name": meta.get("name", d.name),
            "description": meta.get("description", ""),
            "triggers": meta.get("triggers", []),
            "when_to_use": meta.get("whenToUse", ""),
            "dir": str(d),
            "has_templates": has_templates,
            "has_examples": has_examples,
        })
    return result


def load_skill(name: str) -> Optional[dict]:
    """Carga el contenido completo de un skill."""
    content = _read_skill_md(name)
    if not content:
        return None
    meta, body = _parse_frontmatter(content)
    skill_dir = SKILLS_DIR / name

    result = {
        "name": meta.get("name", name),
        "description": meta.get("description", ""),
        "triggers": meta.get("triggers", []),
        "when_to_use": meta.get("whenToUse", ""),
        "body": body,
        "templates": {},
        "examples": {},
    }

    for sub in ["templates", "examples"]:
        subdir = skill_dir / sub
        if subdir.exists():
            for f in subdir.iterdir():
                if f.is_file():
                    result[sub][f.name] = f.read_text(encoding="utf-8")

    return result


def load_active_skills(query: str, max_skills: int = 3) -> list[dict]:
    """
    Carga skills relevantes para una consulta por keyword matching en triggers.
    Retorna los skills mas relevantes (maximo max_skills).
    """
    all_skills = list_skills()
    if not all_skills:
        return []

    q_words = set(query.lower().split())
    scored = []
    for skill in all_skills:
        triggers = skill.get("triggers", [])
        if not triggers:
            continue
        trigger_text = " ".join(triggers).lower()
        hits = sum(1 for w in q_words if w in trigger_text)
        if hits > 0:
            scored.append((hits, skill))

    scored.sort(key=lambda x: x[0], reverse=True)
    active = []
    for _, skill in scored[:max_skills]:
        loaded = load_skill(skill["name"])
        if loaded:
            active.append(loaded)

    return active


def load_skills_context(query: str) -> str:
    """
    Construye el contexto de skills activos para incluir en el system prompt.
    """
    active = load_active_skills(query)
    if not active:
        return ""

    parts = ["\n─── SKILLS ACTIVOS ───\n"]
    for skill in active:
        parts.append(f"\n### Skill: {skill['name']}")
        parts.append(f"**Cuando usar:** {skill['when_to_use']}")
        parts.append(f"\n{skill['body']}")

        templates = skill.get("templates", {})
        if templates:
            parts.append("\n**Plantillas de referencia:**")
            for tpl_name, tpl_content in templates.items():
                parts.append(f"\n#### {tpl_name}\n```\n{tpl_content[:2000]}\n```")

        examples = skill.get("examples", {})
        if examples:
            parts.append("\n**Ejemplos:**")
            for ex_name, ex_content in examples.items():
                parts.append(f"\n#### {ex_name}\n```\n{ex_content[:2000]}\n```")

    return "\n".join(parts)


def delete_skill(name: str) -> bool:
    """Elimina un skill y todo su contenido."""
    skill_dir = SKILLS_DIR / name
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
        return True
    return False


def get_skill_templates(name: str) -> dict[str, str]:
    """Obtiene las plantillas de un skill."""
    skill_dir = SKILLS_DIR / name
    templates_dir = skill_dir / "templates"
    if not templates_dir.exists():
        return {}
    return {f.name: f.read_text(encoding="utf-8") for f in templates_dir.iterdir() if f.is_file()}
