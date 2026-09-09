"""
Research Agent — ImpuestIA (Fase 5)

Orquesta busqueda web (Tavily) + scraping + guardado en vault Obsidian.
Integrado con live_lookup.py para busqueda y obsidian_writer.py para persistencia.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_module
import json
from http_security import tls_context
from datetime import date, datetime
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

FETCH_TIMEOUT = 30


async def _fetch_page(url: str) -> str:
    """Descarga el contenido textual de una pagina web."""
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True, verify=tls_context()) as client:
            resp = await client.get(url, headers={
                "User-Agent": "ImpuestIA/1.0 (tax research agent; contacto@impuestia.cl)",
            })
            resp.raise_for_status()
            if resp.content.startswith(b'%PDF'):
                def extract_pdf():
                    import fitz
                    with fitz.open(stream=resp.content, filetype='pdf') as document:
                        return '\n'.join(page.get_text() for page in document)
                return '<p>' + html_module.escape(await asyncio.to_thread(extract_pdf)) + '</p>'
            # El SII conserva páginas históricas en Windows-1252.
            if 'charset=' not in resp.headers.get('content-type', '').lower():
                try:
                    text = resp.content.decode('utf-8')
                except UnicodeDecodeError:
                    text = resp.content.decode('cp1252', errors='replace')
            else:
                text = resp.text
            return text
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


def _clean_text(text: str, max_chars: int | None = None) -> str:
    """Limpia texto extraído; las fuentes se conservan completas internamente."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    cleaned = "\n".join(lines)
    if max_chars is not None and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "\n\n[... texto truncado por longitud ...]"
    return cleaned


async def _scrape_result(result: dict) -> dict:
    """Scrapea una pagina de resultado para obtener texto completo."""
    if result.get("local"):
        return {**result, "fetched": True, "retrieved_at": datetime.now().isoformat(),
                "content_hash": hashlib.sha256(result.get("full_text", "").encode()).hexdigest()}
    url = result.get("url", "")
    title = result.get("title", "")
    content = result.get("content", "")

    full_text = ""
    if url:
        html = await _fetch_page(url)
        if html:
            full_text = _extract_text_from_html(html)

    return {
        "official": result.get("official", False),
        "curated": result.get("curated", False),
        "title": title,
        "url": url,
        "snippet": content,
        "full_text": _clean_text(full_text) if full_text else content,
        "fetched": bool(full_text.strip()),
        "retrieved_at": datetime.now().isoformat(),
        "content_hash": hashlib.sha256(full_text.encode()).hexdigest() if full_text else None,
    }


async def search_and_scrape(
    query: str,
    *,
    scrape_full: bool = True,
    max_results: int | None = None,
) -> list[dict]:
    """Busca en fuentes oficiales y opcionalmente scrapea el texto completo."""
    raw_results = await live_lookup.search_live(query, strict=True)
    if max_results is not None:
        raw_results = raw_results[:max_results]
    if not raw_results:
        console.print(f"[yellow][RESEARCH] Sin resultados para: {query[:80]}[/yellow]")
        return []

    if scrape_full:
        enriched = await _scrape_all(raw_results)
        results = []
        for i, r in enumerate(enriched):
            if isinstance(r, Exception):
                results.append({**raw_results[i], "full_text": raw_results[i].get("content", "")})
            else:
                results.append(r)
        return results

    return [{**r, "snippet": r["content"], "full_text": r["content"]} for r in raw_results]


async def _scrape_all(results: list[dict]) -> list[dict | Exception]:
    """Descarga cada fuente pertinente con concurrencia acotada, sin cortar la lista."""
    semaphore = asyncio.Semaphore(config.RESEARCH_FETCH_CONCURRENCY)

    async def fetch(source: dict) -> dict:
        async with semaphore:
            return await _scrape_result(source)

    return await asyncio.gather(*(fetch(source) for source in results), return_exceptions=True)


def _parse_law_window(text: str, facts_date: str | None = None) -> tuple[str, str]:
    """Distingue una copia histórica de una aplicable a la fecha del caso."""
    import re
    match = re.search(r"Fin Vigencia:\s*(\d{2}-[A-Z]{3}-\d{4})", text[:2500])
    if not match:
        return "vigente_por_verificar", ""
    months = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12}
    day, month, year = match.group(1).split("-")
    end = date(int(year), months[month], int(day))
    if facts_date:
        try:
            if end >= date.fromisoformat(facts_date):
                return "version_aplicable_a_fecha_hechos", match.group(1)
        except ValueError:
            pass
    if end < date.today():
        return "version_historica", match.group(1)
    return "vigente_por_verificar", match.group(1)


def _local_law_sources(query: str, facts_date: str | None = None) -> list[dict]:
    """Expone artículos completos de las leyes cargadas como evidencia oficial.

    La selección reduce el contexto para el modelo, pero nunca sustituye el
    archivo original: cada resultado indica versión y artículo para que la
    respuesta pueda ser comprobada contra el documento local y LeyChile.
    """
    import re
    from context_rag.law_loader import law_loader
    from context_rag.prompt_builder import _score_articles

    sources: list[dict] = []
    for law in law_loader.all():
        scored = _score_articles(query, law)
        selected = [number for number, score in scored if score > 0][:3]
        if not selected and scored:
            selected = [scored[0][0]]
        url_match = re.search(r"Url Corta:\s*(https?://\S+)", law.text[:3000], re.I)
        url = url_match.group(1) if url_match else "https://www.bcn.cl/leychile/"
        legal_status, window = _parse_law_window(law.text, facts_date)
        for number in selected:
            article = law.article(number)
            if not article:
                continue
            sources.append({
                "official": True, "curated": True, "local": True,
                "title": f"{law.name} — Artículo {number}", "url": url,
                "snippet": article[:600], "content": article[:600], "full_text": article,
                "location": f"Artículo {number}; copia local; {window or 'vigencia sin fecha final declarada'}",
                "legal_status": legal_status,
            })
    return sources


def _stored_official_sources(query: str) -> list[dict]:
    """Aprovecha documentos oficiales ya descargados sin volver a buscarlos."""
    try:
        from production_store import store
        rows = store.full_official_documents(query, limit=8)
    except Exception:
        return []
    sources = []
    for row in rows:
        metadata = json.loads(row.get("metadata_json") or "{}")
        complete = metadata.get("complete_text", True)
        location = metadata.get("location") or row.get("document_type", "documento oficial")
        related = [relation["target"] for relation in row.get("relations", [])
                   if relation.get("relation_type") == "relacionado_con"]
        if related:
            location += "; " + " | ".join(related[:4])
        if row.get("historical_archive"):
            location = "Archivo histórico (anterior a 2024); " + location
        sources.append({
            "official": True, "curated": True, "local": bool(complete), "title": row["title"], "url": row["url"],
            "snippet": row["body"][:600], "content": row["body"][:600],
            # Una ficha incompleta se vuelve a descargar desde su URL oficial
            # antes de que pueda llegar a la etapa de evidencia.
            "full_text": row["body"] if complete else "", "location": location,
            "legal_status": row.get("legal_status", "por_verificar"),
        })
    return sources


def save_research_to_vault(
    query: str,
    results: list[dict],
    *,
    cliente: Optional[str] = None,
    carpeta: str = "Jurisprudencia",
    analysis: str = "",
    evidence: list[dict] | None = None,
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
        content=(analysis + '\n\n' if analysis else '') + _build_summary(query, results, saved_files)
                + ('\n\n## Pasajes comprobados\n' + '\n\n'.join(
                    f"{e['statement']}\n\n> {e['quote']}\n\nFuente: {e['url']}" for e in evidence) if evidence else ''),
        title=f"Investigacion: {query[:60]}",
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
        "## Fuentes respaldatorias",
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
    for i, r in enumerate(results, 1):
        title = r.get("title", f"Resultado {i}")
        url = r.get("url", "")
        snippet = r.get("snippet", "")[:250]
        lines.append(f"{i}. *{title}*")
        lines.append(f"   {snippet}")
        if url:
            lines.append(f"   {url}")
        lines.append("")

    return "\n".join(lines)


async def run_research(
    query: str,
    *,
    cliente: Optional[str] = None,
    fecha_hechos: str | None = None,
    run_id: str | None = None,
    _budget=None,
    include_local_laws: bool = False,
    _provider: str | None = None,
    _allow_fallback: bool = True,
) -> dict:
    """
    Flujo completo de investigacion:
    buscar → scrapear → guardar en vault → retornar resumen.
    """
    from llm_client import LLMClient
    from privacy_guard import redact_for_external
    from research_quality import (apply_findings, plan_research, reference_sources, relevant,
                                  relevance_score, substantiate, render_applied_report, render_findings)
    query = query.strip()
    if not query:
        raise ValueError("Escribe una pregunta para investigar.")
    redaction = redact_for_external(query)
    external_query = redaction.text
    case_context = external_query
    if fecha_hechos:
        case_context += f"\nFecha de los hechos que debe regir el análisis: {fecha_hechos}."
    provider = _provider or getattr(config, 'RESEARCH_LLM_PROVIDER', '') or ('openai' if config.OPENAI_API_KEY else None)
    model = LLMClient(provider=provider)
    base = {"query": external_query, "has_evidence": False, "archivos": [], "resumen": "",
            "sources": [], "warnings": [], "quality": "insufficient_evidence", "run_id": run_id,
            "fecha_hechos": fecha_hechos, "privacy": {"redactions": redaction.replacements}}
    if redaction.replacements:
        base["warnings"].append("Se omitieron identificadores personales antes de consultar buscadores y modelos externos.")
    try:
        plan = await plan_research(model, case_context, budget=_budget)
        base["search_queries"] = plan.queries
        batches = await asyncio.gather(*(live_lookup.search_live(q, strict=True)
                                        for q in plan.queries), return_exceptions=True)
        references = reference_sources(case_context)
        candidates = {s['url']: s for s in references}
        for batch in batches:
            if isinstance(batch, Exception):
                base["warnings"].append("Una de las búsquedas falló; se continuó con las restantes.")
                continue
            for source in batch:
                if relevant(source, plan.concepts):
                    candidates.setdefault(source['url'], source)
        # Las leyes entregadas por el usuario y la biblioteca sincronizada se
        # agregan después de los resultados específicos hallados para que una
        # fuente local general no opaque un oficio o fallo directamente aplicable.
        library_sources = _stored_official_sources(case_context)
        if include_local_laws:
            library_sources = [*_local_law_sources(case_context, fecha_hechos), *library_sources]
        for source in library_sources:
            candidates.setdefault(source["url"] + "#" + source.get("location", ""), source)
        reference_urls = {s['url'] for s in references}
        ordered = sorted(candidates.values(), key=lambda s: (s['url'] in reference_urls, relevance_score(s, plan.concepts), s.get('official', False)), reverse=True)
        enriched = await _scrape_all(ordered)
        sources = [s for s in enriched if isinstance(s, dict) and s.get('fetched')
                   and relevant(s, plan.concepts, full_document=True)]
        # Vigencia: excluir fuentes marcadas como derogadas o históricas no aplicables.
        _unusable = [s['title'] for s in sources if (s.get('legal_status') or '').lower() == 'derogada']
        sources = [s for s in sources if (s.get('legal_status') or '').lower() != 'derogada']
        if _unusable:
            base["warnings"].append(f"Se excluyeron fuentes no vigentes o históricas ({len(_unusable)}): " + "; ".join(_unusable[:4]) + ("…" if len(_unusable) > 4 else ""))
        if not sources:
            base["reply"] = "No pude reunir documentos pertinentes y legibles para respaldar esta consulta. No se generaron conclusiones ni se guardaron fuentes irrelevantes. Intenta nuevamente o aporta un documento del caso."
            return base
        if _budget is None:
            claims, missing = await substantiate(model, case_context, sources)
        else:
            claims, missing = await substantiate(model, case_context, sources, budget=_budget)
        if not claims:
            base["reply"] = "Encontré documentos, pero sus pasajes no permitieron sostener una respuesta después de revisar las citas. No se guardaron notas ni se emitieron conclusiones sin respaldo."
            return base
        used = {c.source_id for c in claims}
        selected = [s for i, s in enumerate(sources, 1) if i in used]
        try:
            report = await apply_findings(model, case_context, claims, missing, sources, budget=_budget)
            reply = render_applied_report(report, claims, sources, missing=missing)
            quality = "complete" if not report.missing else "partial"
            base["report"] = report.model_dump()
        except Exception as exc:
            console.print(f"[yellow][RESEARCH] Síntesis aplicada falló ({type(exc).__name__}): {str(exc)[:300]}[/yellow]")

            base["warnings"].append("No se pudo completar la síntesis aplicada; se muestran los hallazgos comprobados.")
            reply = render_findings(claims, missing, sources)
            quality = "supported_findings"
        base.update(reply=reply, has_evidence=True,
                    quality=quality, sources=[{k: s.get(k) for k in
                    ('title', 'url', 'official', 'retrieved_at', 'content_hash', 'location', 'legal_status')} for s in selected],
                    evidence=[{"statement": c.statement, "quote": c.quote,
                               "url": sources[c.source_id - 1]['url'],
                               "location": sources[c.source_id - 1].get('location', '')} for c in claims])
        try:
            saved = await asyncio.to_thread(save_research_to_vault, external_query, selected, cliente=cliente,
                                          analysis=base['reply'], evidence=base['evidence'])
            base.update(archivos=saved['archivos'], resumen=saved['resumen'])
        except Exception:
            base['warnings'].append("La respuesta está disponible, pero no se pudieron guardar las notas.")
        return base
    except Exception as exc:
        error = str(exc).lower()
        exhausted = any(marker in error for marker in ('insufficient_quota', 'no credits remaining', 'rate limit'))
        if (_allow_fallback and exhausted and model.provider != 'deepseek'
                and getattr(config, 'DEEPSEEK_API_KEY', '')):
            await model.aclose()
            model = None
            return await run_research(query, cliente=cliente, _provider='deepseek', _allow_fallback=False)
        raise
    finally:
        if model:
            await model.aclose()



def run_research_sync(
    query: str,
    *,
    cliente: Optional[str] = None,
) -> dict:
    """Wrapper sincrono para run_research."""
    return asyncio.run(run_research(query, cliente=cliente))
