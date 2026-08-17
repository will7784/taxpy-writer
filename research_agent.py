"""
Research Agent — ImpuestIA (Fase 5)

Orquesta busqueda web (Tavily) + scraping + guardado en vault Obsidian.
Integrado con live_lookup.py para busqueda y obsidian_writer.py para persistencia.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from rich.console import Console

import config
import live_lookup
from obsidian_writer import (
    VAULT,
    write_jurisprudencia,
    write_note,
    write_analisis,
)

console = Console()

FETCH_TIMEOUT = 20


async def _fetch_page(url: str) -> str:
    """Descarga el contenido textual de una pagina web."""
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "ImpuestIA/1.0 (tax research agent; contacto@impuestia.cl)",
            })
            resp.raise_for_status()
            return resp.text[:50000]
    except Exception as e:
        console.print(f"[yellow][RESEARCH] Fetch fallo para {url}: {e}[/yellow]")
        return ""


def _extract_text_from_html(html: str) -> str:
    """Extrae texto legible de HTML basico."""
    try:
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
                self.skip = False

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "nav", "footer", "header"):
                    self.skip = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "nav", "footer", "header"):
                    self.skip = False
                if tag in ("p", "br", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
                    self.text.append("\n")

            def handle_data(self, data):
                if not self.skip and data.strip():
                    self.text.append(data.strip())

        extractor = TextExtractor()
        extractor.feed(html)
        return "\n".join(extractor.text)
    except Exception:
        return html


def _clean_text(text: str, max_chars: int = 10000) -> str:
    """Limpia y trunca texto extraido."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    cleaned = "\n".join(lines)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "\n\n[... texto truncado por longitud ...]"
    return cleaned


async def _scrape_result(result: dict) -> dict:
    """Scrapea una pagina de resultado para obtener texto completo."""
    url = result.get("url", "")
    title = result.get("title", "")
    content = result.get("content", "")

    full_text = ""
    if url:
        html = await _fetch_page(url)
        if html:
            full_text = _extract_text_from_html(html)

    return {
        "title": title,
        "url": url,
        "snippet": content,
        "full_text": _clean_text(full_text) if full_text else content,
    }


async def search_and_scrape(
    query: str,
    *,
    max_results: int = 3,
    scrape_full: bool = True,
) -> list[dict]:
    """Busca en fuentes oficiales y opcionalmente scrapea el texto completo."""
    raw_results = await live_lookup.search_live(query, max_results=max_results)
    if not raw_results:
        console.print(f"[yellow][RESEARCH] Sin resultados para: {query[:80]}[/yellow]")
        return []

    if scrape_full:
        tasks = [_scrape_result(r) for r in raw_results]
        enriched = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for i, r in enumerate(enriched):
            if isinstance(r, Exception):
                results.append({**raw_results[i], "full_text": raw_results[i].get("content", "")})
            else:
                results.append(r)
        return results

    return [{"title": r["title"], "url": r["url"], "snippet": r["content"], "full_text": r["content"]} for r in raw_results]


def save_research_to_vault(
    query: str,
    results: list[dict],
    *,
    cliente: Optional[str] = None,
    carpeta: str = "Jurisprudencia",
) -> dict:
    """
    Guarda los resultados de investigacion como archivos Markdown en el vault.

    Returns:
        dict con paths de archivos creados y resumen
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved_files = []

    for i, r in enumerate(results):
        title = r.get("title", f"Resultado {i+1}")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        full_text = r.get("full_text", snippet)

        safe_name = f"research_{timestamp}_{i+1}"

        body_parts = [
            f"## Fuente",
            f"- **URL:** {url}",
            f"- **Fecha de consulta:** {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            f"- **Consulta original:** {query}",
            "",
            "## Resumen (snippet)",
            snippet,
            "",
            "## Texto completo extraido",
            full_text,
        ]

        content = "\n".join(body_parts)
        tags = _extract_tags(query, full_text)

        path = write_jurisprudencia(
            content=content,
            cliente=cliente,
            filename=safe_name,
            tags=tags,
            fuente=url,
        )
        saved_files.append(str(path))

    summary_path = write_note(
        folder=f"Clientes/{cliente}/Notas" if cliente else "00_Indice",
        filename=f"research_summary_{timestamp}",
        content=_build_summary(query, results, saved_files),
        titulo=f"Investigacion: {query[:60]}",
        tipo="investigacion",
        cliente=cliente,
        tags=["research", "investigacion"],
        fuentes=[r["url"] for r in results if r.get("url")],
    )

    return {
        "query": query,
        "resultados": len(results),
        "archivos": saved_files,
        "resumen": str(summary_path),
    }


def _extract_tags(query: str, text: str) -> list[str]:
    """Extrae tags relevantes de la query y el texto."""
    tags = set()
    keyword_map = {
        "prescripcion": ["prescripcion", "art200", "art201"],
        "citacion": ["citacion", "art63", "fiscalizacion"],
        "liquidacion": ["liquidacion", "giro", "art24"],
        "pyme": ["pyme", "pro-pyme", "art14d"],
        "renta": ["renta", "lir", "dl824"],
        "iva": ["iva", "dl825", "credito-fiscal"],
        "inmueble": ["inmueble", "art17", "bien-raiz"],
        "observacion": ["observacion", "g113", "g22"],
        "recurso": ["recurso", "reposicion", "reclamacion"],
        "sancion": ["sancion", "multa", "infraccion"],
        "facilidades": ["facilidades-pago", "art192", "convenio"],
    }

    combined = (query + " " + text[:2000]).lower()
    for key, tag_list in keyword_map.items():
        if key in combined:
            tags.update(tag_list)

    tags.add("research")
    return sorted(list(tags))[:8]


def _build_summary(query: str, results: list[dict], saved_files: list[str]) -> str:
    """Construye un resumen en Markdown de la investigacion."""
    lines = [
        f"# Investigacion: {query}",
        f"_Realizada el {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
        "",
        f"## Resultados ({len(results)})",
    ]

    for i, r in enumerate(results):
        title = r.get("title", f"Resultado {i+1}")
        url = r.get("url", "")
        snippet = r.get("snippet", "")[:300]
        lines.append(f"\n### {i+1}. {title}")
        lines.append(f"- **URL:** {url}")
        lines.append(f"- **Resumen:** {snippet}")
        if i < len(saved_files):
            filepath = saved_files[i]
            vault_relative = _vault_relative(filepath)
            lines.append(f"- **Archivo:** [[{Path(filepath).stem}]]")

    lines.append(f"\n## Archivos guardados")
    for f in saved_files:
        lines.append(f"- [[{Path(f).stem}]]")

    return "\n".join(lines)


def _vault_relative(absolute_path: str) -> str:
    """Convierte ruta absoluta a relativa al vault."""
    try:
        return str(Path(absolute_path).relative_to(VAULT))
    except ValueError:
        return absolute_path


def format_research_reply(results: list[dict], query: str) -> str:
    """Formatea resultados para respuesta de Telegram."""
    if not results:
        return f"No encontre resultados para: _{query}_\n\nPrueba con terminos mas especificos o revisa las fuentes oficiales directamente en sii.cl o bcn.cl."

    lines = [f"*Resultados de investigacion:* {query}\n"]
    for i, r in enumerate(results[:5], 1):
        title = r.get("title", f"Resultado {i}")
        url = r.get("url", "")
        snippet = r.get("snippet", "")[:250]
        lines.append(f"{i}. *{title}*")
        lines.append(f"   {snippet}")
        if url:
            lines.append(f"   {url}")
        lines.append("")

    lines.append("_Resultados guardados en el vault de Obsidian._")
    return "\n".join(lines)


async def run_research(
    query: str,
    *,
    cliente: Optional[str] = None,
    max_results: int = 3,
) -> dict:
    """
    Flujo completo de investigacion:
    buscar → scrapear → guardar en vault → retornar resumen.
    """
    console.print(f"[cyan][RESEARCH] Investigando: {query[:80]}[/cyan]")
    results = await search_and_scrape(query, max_results=max_results)
    if not results:
        return {"query": query, "resultados": 0, "archivos": [], "resumen": "", "reply": format_research_reply([], query)}

    saved = save_research_to_vault(query, results, cliente=cliente)
    saved["reply"] = format_research_reply(results, query)
    return saved


def run_research_sync(
    query: str,
    *,
    cliente: Optional[str] = None,
    max_results: int = 3,
) -> dict:
    """Wrapper sincrono para run_research."""
    return asyncio.run(run_research(query, cliente=cliente, max_results=max_results))
