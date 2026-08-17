"""
Exportador de contenido a archivos descargables.
Soporta Markdown (con frontmatter + wikilinks), DOCX, y Obsidian vault.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Optional


def to_markdown(
    content: str,
    title: str,
    *,
    frontmatter: Optional[dict] = None,
    wikilinks: bool = False,
) -> bytes:
    """Genera bytes UTF-8 de un archivo Markdown, con frontmatter YAML opcional."""
    parts = []
    if frontmatter:
        parts.append("---")
        for k, v in frontmatter.items():
            if isinstance(v, list):
                parts.append(f"{k}: [{', '.join(str(x) for x in v)}]")
            else:
                parts.append(f'{k}: "{v}"')
        parts.append("---\n")

    parts.append(f"# {title}\n")
    parts.append(f"_Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}_\n")
    parts.append("---\n")

    body = content
    if wikilinks:
        body = _convert_to_wikilinks(body)

    parts.append(body)
    parts.append("")
    return "\n".join(parts).encode("utf-8")


def _convert_to_wikilinks(text: str) -> str:
    """Convierte referencias a formato [[wikilink]] de Obsidian."""
    patterns = [
        (r'Art(?:ículo|\.)\s+(\d+)(?:\s+(?:letra\s+)?([A-H]))?(?:\s+(?:N°|Nº|número|numeral)\s+(\d+))?', _wikilink_art),
        (r'(DL[- ]824|DL[- ]825|DL[- ]830)', lambda m: f"[[{m.group(0)}]]"),
        (r'(Ley (?:sobre )?Impuesto a la Renta)', lambda m: f"[[DL-824 — {m.group(0)}]]"),
        (r'(Código Tributario)', lambda m: f"[[DL-830 — {m.group(0)}]]"),
    ]
    result = text
    for pattern, replacer in patterns:
        result = re.sub(pattern, replacer, result, flags=re.IGNORECASE)
    return result


def _wikilink_art(match) -> str:
    """Construye un wikilink para un articulo."""
    art = match.group(1)
    letra = match.group(2)
    numeral = match.group(3)
    parts = [f"Art. {art}"]
    if letra:
        parts.append(f"Letra {letra}")
    if numeral:
        parts.append(f"N°{numeral}")
    return f"[[{' '.join(parts)}]]"


def to_docx(content: str, title: str) -> bytes:
    """Genera bytes de un archivo DOCX desde contenido Markdown-like."""
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # Título
    heading = doc.add_heading(title, level=0)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Fecha
    date_para = doc.add_paragraph()
    date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = date_para.add_run(f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    run.italic = True
    run.font.size = Pt(10)

    doc.add_paragraph()  # espacio

    # Parsear contenido línea por línea
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Heading 1
        if stripped.startswith("# ") and not stripped.startswith("## "):
            doc.add_heading(stripped[2:], level=1)
            i += 1
            continue

        # Heading 2
        if stripped.startswith("## ") and not stripped.startswith("### "):
            doc.add_heading(stripped[3:], level=2)
            i += 1
            continue

        # Heading 3
        if stripped.startswith("### "):
            doc.add_heading(stripped[4:], level=3)
            i += 1
            continue

        # Listas con guion o asterisco
        if re.match(r"^[-*]\s+", stripped):
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(re.sub(r"^[-*]\s+", "", stripped))
            i += 1
            continue

        # Listas numeradas
        if re.match(r"^\d+\.\s+", stripped):
            p = doc.add_paragraph(style="List Number")
            p.add_run(re.sub(r"^\d+\.\s+", "", stripped))
            i += 1
            continue

        # Párrafo normal (con soporte básico de **bold**)
        p = doc.add_paragraph()
        _add_formatted_text(p, stripped)
        i += 1

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


def _add_formatted_text(paragraph, text: str) -> None:
    """Agrega texto a un párrafo respetando **bold** y *italic*."""
    # Patrón para **bold** y *italic* (no doble)
    pattern = r"(\*\*[^*]+\*\*|\*[^*]+\*)"
    parts = re.split(pattern, text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        elif part.startswith("*") and part.endswith("*"):
            run = paragraph.add_run(part[1:-1])
            run.italic = True
        else:
            paragraph.add_run(part)
