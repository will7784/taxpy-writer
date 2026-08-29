"""
Ebook Writer — ImpuestIA (Fase 6)

Pipeline completo de redaccion de ebooks tributarios:
  1. RESEARCH: busca fuentes legales + jurisprudencia
  2. ESTRUCTURA: genera indice de capitulos con LLM
  3. REDACCIÓN: capitulo por capitulo con ejemplos, tablas, citas
  4. EXPORTACIÓN: EPUB + PDF + Markdown

Integrado con research_agent, context_rag, y obsidian_writer.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console

import config
import exporter
from context_rag import build_for_chat
from obsidian_writer import VAULT, write_note
from research_agent import run_research

console = Console()

EBOOKS_DIR = config.BASE_DIR / "exports" / "ebooks"
EBOOKS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class EbookOutline:
    titulo: str
    tema: str
    audiencia: str = "contadores, abogados y empresarios"
    capitulos: list[dict] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            f"# {self.titulo}",
            f"_Audiencia: {self.audiencia}_",
            f"_Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
            "",
            "## Indice",
        ]
        for i, cap in enumerate(self.capitulos, 1):
            lines.append(f"{i}. **{cap['titulo']}** — {cap.get('descripcion', '')}")
            for sub in cap.get("subtemas", []):
                lines.append(f"   - {sub}")
        return "\n".join(lines)


@dataclass
class CapituloRedactado:
    numero: int
    titulo: str
    contenido: str
    fuentes: list[str] = field(default_factory=list)
    ejemplos: list[str] = field(default_factory=list)


async def generate_outline(
    tema: str,
    *,
    audiencia: str = "contadores, abogados y empresarios",
    llm_client=None,
) -> EbookOutline:
    """
    Genera el indice del ebook usando el LLM con el contexto legal cargado.
    """
    console.print(f"[cyan][EBOOK] Generando outline para: {tema[:80]}[/cyan]")

    research_context = ""
    try:
        research = await run_research(f"doctrina {tema}", max_results=2)
        if research.get("resultados", 0) > 0:
            research_context = research.get("reply", "")[:3000]
    except Exception as e:
        console.print(f"[yellow][EBOOK] Research fallo (no critico): {e}[/yellow]")

    titulo = _generate_title(tema)

    system = (
        "Eres un abogado tributario chileno experto en escribir libros practicos.\n"
        f"Vas a generar el indice de un ebook titulado '{titulo}'.\n"
        f"Audiencia objetivo: {audiencia}.\n\n"
        "REGLAS:\n"
        "1. Estructura: 5-8 capitulos logicos, cada uno con 2-4 subtemas.\n"
        "2. Cada capitulo debe cubrir un aspecto practico concreto, no teoria abstracta.\n"
        "3. Incluye al menos un capitulo de ejemplos practicos o casos reales.\n"
        "4. Incluye un capitulo de conclusiones y recomendaciones.\n"
        "5. Los titulos deben ser atractivos y descriptivos (no academicos).\n\n"
        "Responde SOLO en JSON con esta estructura:\n"
        '{"titulo": "...", "capitulos": [{"titulo": "...", "descripcion": "...", "subtemas": ["..."]}]}'
    )

    user = f"Tema del ebook: {tema}"
    if research_context:
        user += f"\n\nInvestigacion preliminar:\n{research_context[:2000]}"

    try:
        from context_rag import law_loader
        lir = law_loader.get("lir")
        if lir:
            arts = lir.article("14") or lir.article("17") or lir.article("20") or ""
            user += f"\n\nContexto legal relevante:\n{arts[:3000]}"
    except Exception:
        pass

    if llm_client:
        try:
            raw = await llm_client.chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.4,
                max_tokens=2000,
            )
            data = _parse_json(raw)
            if data:
                return EbookOutline(
                    titulo=data.get("titulo", titulo),
                    tema=tema,
                    audiencia=audiencia,
                    capitulos=data.get("capitulos", []),
                )
        except Exception as e:
            console.print(f"[red][EBOOK] Error generando outline: {e}[/red]")

    return _default_outline(titulo, tema, audiencia)


def _generate_title(tema: str) -> str:
    titulo = tema.strip().rstrip(".,;:")
    if len(titulo) < 10:
        titulo = f"Guia Practica de {titulo}"
    if not any(c.isupper() for c in titulo[1:]):
        titulo = titulo[0].upper() + titulo[1:]
    return titulo


def _parse_json(raw: str) -> Optional[dict]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _default_outline(titulo: str, tema: str, audiencia: str) -> EbookOutline:
    return EbookOutline(
        titulo=titulo,
        tema=tema,
        audiencia=audiencia,
        capitulos=[
            {"titulo": "Introduccion y Marco Normativo", "descripcion": "Contexto legal y normativa aplicable", "subtemas": ["Base legal", "Ambito de aplicacion", "Conceptos clave"]},
            {"titulo": "Regimen General", "descripcion": "Reglas generales aplicables", "subtemas": ["Sujetos", "Hecho gravado", "Obligaciones"]},
            {"titulo": "Regimenes Especiales", "descripcion": "Excepciones y regimenes especiales", "subtemas": ["PYME", "Simplificado", "Transparencia"]},
            {"titulo": "Ejemplos Practicos", "descripcion": "Casos reales paso a paso", "subtemas": ["Caso 1", "Caso 2", "Caso 3"]},
            {"titulo": "Errores Comunes y Como Evitarlos", "descripcion": "Errores frecuentes en la practica", "subtemas": ["Error en calculos", "Error en plazos", "Error en documentacion"]},
            {"titulo": "Jurisprudencia Relevante", "descripcion": "Fallos y pronunciamientos clave", "subtemas": ["SII", "TTA", "Corte Suprema"]},
            {"titulo": "Conclusiones y Recomendaciones", "descripcion": "Resumen ejecutivo y pasos a seguir", "subtemas": ["Checklist", "Proximos pasos", "Recursos adicionales"]},
        ],
    )


async def write_chapter(
    outline: EbookOutline,
    chapter_index: int,
    *,
    llm_client=None,
    previous_chapters: str = "",
) -> CapituloRedactado:
    """Redacta un capitulo individual del ebook."""
    if chapter_index < 0 or chapter_index >= len(outline.capitulos):
        raise ValueError(f"Capitulo {chapter_index} fuera de rango")

    cap = outline.capitulos[chapter_index]
    console.print(f"[cyan][EBOOK] Redactando capitulo {chapter_index + 1}/{len(outline.capitulos)}: {cap['titulo']}[/cyan]")

    system = (
        f"Eres un abogado tributario chileno escribiendo el capitulo {chapter_index + 1} "
        f"del ebook '{outline.titulo}'.\n"
        f"Audiencia: {outline.audiencia}.\n\n"
        "REGLAS DE REDACCION:\n"
        "1. Extension: 800-1500 palabras.\n"
        "2. Estructura: introduccion breve → desarrollo con subtitulos → ejemplo practico → conclusion parcial.\n"
        "3. Incluye al menos UN ejemplo practico con sujetos, hechos y resultado.\n"
        "4. Incluye al menos DOS citas legales exactas (articulo, ley).\n"
        "5. Usa tablas comparativas en Markdown donde sea util.\n"
        "6. Tono profesional pero accesible (ni academico ni coloquial).\n"
        "7. NO uses markdown de headings nivel 1 (#), solo ## y ###.\n"
        "8. Al final, incluye una seccion 'Fuentes citadas en este capitulo'.\n\n"
        "Estructura del ebook completo:\n"
        f"{outline.to_markdown()}"
    )

    user = (
        f"Redacta el capitulo {chapter_index + 1}: *{cap['titulo']}*\n"
        f"Descripcion: {cap.get('descripcion', '')}\n"
        f"Subtemas a cubrir: {', '.join(cap.get('subtemas', []))}\n"
    )
    if previous_chapters:
        user += f"\n\nCapitulos anteriores (para contexto):\n{previous_chapters[:2000]}"

    if llm_client:
        try:
            content = await llm_client.chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
                max_tokens=4000,
            )
            return CapituloRedactado(
                numero=chapter_index + 1,
                titulo=cap["titulo"],
                contenido=content.strip() if content else "",
            )
        except Exception as e:
            console.print(f"[red][EBOOK] Error capitulo {chapter_index + 1}: {e}[/red]")

    return CapituloRedactado(
        numero=chapter_index + 1,
        titulo=cap["titulo"],
        contenido=f"## {cap['titulo']}\n\n_Error generando este capitulo. Reintente._\n",
    )


async def write_ebook(
    tema: str,
    *,
    audiencia: str = "contadores, abogados y empresarios",
    llm_client=None,
    progress_callback=None,
) -> dict:
    """
    Pipeline completo: outline → capitulos → exportacion.

    Args:
        tema: tema del ebook
        audiencia: publico objetivo
        llm_client: cliente LLM
        progress_callback: funcion async(chapter_num, total, status) para reportar progreso

    Returns:
        dict con paths de archivos generados
    """
    console.print(f"[bold cyan][EBOOK] Iniciando: {tema[:80]}[/bold cyan]")

    if progress_callback:
        await progress_callback(0, 0, "Generando indice...")

    outline = await generate_outline(tema, audiencia=audiencia, llm_client=llm_client)

    if progress_callback:
        await progress_callback(0, len(outline.capitulos), f"Indice listo: {len(outline.capitulos)} capitulos")

    capitulos = []
    full_text = []
    full_text.append(outline.to_markdown())
    full_text.append("\n---\n")

    for i in range(len(outline.capitulos)):
        prev = "\n".join(full_text[-3:]) if full_text else ""

        if progress_callback:
            await progress_callback(i + 1, len(outline.capitulos), f"Redactando: {outline.capitulos[i]['titulo']}")

        cap = await write_chapter(outline, i, llm_client=llm_client, previous_chapters=prev)
        capitulos.append(cap)
        full_text.append(f"\n## Capitulo {cap.numero}: {cap.titulo}\n{cap.contenido}\n")

    if progress_callback:
        await progress_callback(len(outline.capitulos), len(outline.capitulos), "Exportando formatos...")

    full_content = "\n".join(full_text)
    paths = await _export_ebook(outline, full_content)

    if progress_callback:
        await progress_callback(len(outline.capitulos), len(outline.capitulos), "Completado")

    return {
        "titulo": outline.titulo,
        "capitulos": len(capitulos),
        "contenido": full_content,
        "archivos": paths,
    }


async def _export_ebook(outline: EbookOutline, content: str) -> dict[str, str]:
    """Exporta el ebook a Markdown, y notifica formatos disponibles."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[\\/:*?"<>|]+', "_", outline.titulo)[:60]

    paths = {}

    md_path = EBOOKS_DIR / f"{safe_name}_{timestamp}.md"
    md_path.write_text(content, encoding="utf-8")
    paths["markdown"] = str(md_path)

    try:
        docx_data = exporter.to_docx(content, outline.titulo)
        docx_path = EBOOKS_DIR / f"{safe_name}_{timestamp}.docx"
        docx_path.write_bytes(docx_data)
        paths["docx"] = str(docx_path)
        console.print(f"[green][EBOOK] DOCX exportado: {docx_path.name}[/green]")
    except Exception as e:
        console.print(f"[yellow][EBOOK] DOCX fallo: {e}[/yellow]")

    try:
        vault_path = write_note(
            folder="00_Indice",
            filename=f"ebook_{safe_name}",
            content=content,
            title=outline.titulo,
            tipo="ebook",
            tags=["ebook", "libro"] + [outline.titulo.lower().replace(" ", "-")],
        )
        paths["vault"] = str(vault_path)
        console.print(f"[green][EBOOK] Guardado en vault: {vault_path.name}[/green]")
    except Exception as e:
        console.print(f"[yellow][EBOOK] Vault fallo: {e}[/yellow]")

    return paths
