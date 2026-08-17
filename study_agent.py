"""
Study Agent — ImpuestIA (modo estudio documentado).

Genera un estudio tributario largo, estructurado y citado, combinando TODAS
las fuentes disponibles en una sola pasada:

  1. Arbol de decision validado (si existe para el tema)
  2. Ley completa en contexto largo (context_rag + Kimi/Gemini 1M)
  3. Jurisprudencia y notas propias del usuario (article_index sobre el vault)
  4. Busqueda en vivo opcional (research_agent / Tavily: bcn.cl, sii.cl)

El resultado pasa por el guardrail de citas y se guarda en el vault de
Obsidian (Clientes/<cliente>/Analisis o Estudios/).

Uso:
    from study_agent import run_study
    result = await run_study("venta de inmuebles persona natural", cliente="Nano_Calderon")
    print(result["content"], result["path"])
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

import config
from article_index import article_index, extract_article_refs
from citation_guardrail import guardrail_check
from context_rag import build_context, law_loader
from context_rag.context_router import route_query
from context_rag.prompt_builder import AGENT_MD_PATH
from obsidian_writer import write_note

console = Console()

STUDY_MAX_TOKENS = 8000
STUDY_TEMPERATURE = 0.2
DOC_TEXT_LIMIT = 4000  # chars por documento del vault incluido en el prompt


def _load_agent_md() -> str:
    if AGENT_MD_PATH.exists():
        return AGENT_MD_PATH.read_text(encoding="utf-8")
    return ""


def _study_system_prompt() -> str:
    return (
        f"{_load_agent_md()}\n\n"
        "─── MODO ESTUDIO (DOCUMENTO LARGO) ───\n\n"
        "Estas redactando un ESTUDIO TRIBUTARIO profesional, no una respuesta de chat.\n"
        "Este modo SI usa Markdown y no tiene limite de 250 palabras.\n\n"
        "ESTRUCTURA OBLIGATORIA:\n"
        "1. **Resumen ejecutivo** — la respuesta directa en 3-5 lineas.\n"
        "2. **Hechos y supuestos** — que se asume del caso.\n"
        "3. **Marco normativo** — articulos exactos con cita completa "
        "(ej: 'Articulo 17 N° 8 de la Ley sobre Impuesto a la Renta (DL-824)').\n"
        "4. **Jurisprudencia y doctrina administrativa** — oficios, circulares y "
        "fallos incluidos en las fuentes. Cita numero y fecha si aparecen.\n"
        "5. **Notas del usuario** — integra los documentos propios del usuario si son relevantes.\n"
        "6. **Analisis** — aplica las normas a los hechos, paso a paso.\n"
        "7. **Ejemplo practico** — sujetos, cifras, aplicacion y resultado.\n"
        "8. **Conclusiones y recomendaciones** — acciones concretas.\n"
        "9. **Referencias** — lista de todas las normas y fuentes citadas.\n\n"
        "REGLAS DE RIGOR:\n"
        "- Toda afirmacion de derecho debe tener cita de las fuentes proporcionadas.\n"
        "- Si algo no esta en las fuentes, dilo: 'No tengo esa informacion en mis fuentes indexadas.'\n"
        "- Si un arbol de decision validado esta incluido, su camino tiene PRIORIDAD "
        "sobre tu propia interpretacion.\n"
        "- Montos en UF/UTM/UTA siempre con equivalente aproximado en CLP.\n"
    )


def _tree_reference(query: str) -> str:
    """Si existe un arbol de decision validado para el tema, devuelve su contenido
    como material de referencia priorizado."""
    try:
        from decision_engine import engine as decision_engine
        ranked = decision_engine._rank_trees_keyword(query)
        if not ranked or ranked[0][1] <= 0:
            return ""
        tree = decision_engine._trees.get(ranked[0][0])
        if not tree:
            return ""
    except Exception:
        return ""

    parts = [
        f"ARBOL DE DECISION VALIDADO: {tree.title}",
        f"({tree.law}, Art. {tree.article})",
        "",
    ]
    for node in tree.nodes.values():
        if node.type == "result":
            parts.append(f"[Resultado: {node.summary or node.id}]")
            if node.details:
                parts.append(node.details)
            if node.legal_refs:
                parts.append("Referencias: " + "; ".join(node.legal_refs))
            if node.warnings:
                parts.append("Advertencias: " + "; ".join(node.warnings))
            parts.append("")
    return "\n".join(parts)


def _vault_docs_context(query: str, cliente: Optional[str]) -> tuple[list[str], list[dict]]:
    """Documentos del vault relacionados (jurisprudencia, notas, analisis)."""
    try:
        article_index.rebuild()  # incremental, barato si no hay cambios
    except Exception as e:
        console.print(f"[yellow][STUDY] article_index rebuild fallo: {e}[/yellow]")

    docs = article_index.search(query, max_docs=8, cliente=cliente)
    texts: list[str] = []
    used: list[dict] = []
    for d in docs:
        body = d.load_text()
        body = body[:DOC_TEXT_LIMIT] + ("\n[... truncado ...]" if len(body) > DOC_TEXT_LIMIT else "")
        label = d.tipo.upper() if d.tipo else "DOCUMENTO"
        texts.append(f"[{label}: {d.title} — {Path(d.path).name}]\n{body}")
        used.append({"title": d.title, "path": d.path, "tipo": d.tipo, "refs": d.refs})
    return texts, used


async def run_study(
    topic: str,
    *,
    cliente: Optional[str] = None,
    llm_client=None,
    live: bool = True,
    max_results_live: int = 3,
) -> dict[str, Any]:
    """Genera un estudio tributario completo sobre `topic`."""
    from llm_client import LLMClient

    llm = llm_client or LLMClient()
    console.print(f"[cyan][STUDY] Generando estudio: {topic[:80]} (provider={llm.provider})[/cyan]")

    # 1. Router
    route = await route_query(topic, llm)

    # 2. Arbol de decision validado (si existe)
    tree_ref = _tree_reference(topic)

    # 3. Documentos del vault (jurisprudencia + notas del usuario)
    vault_docs, docs_used = _vault_docs_context(topic, cliente)
    personal_docs = ([tree_ref] if tree_ref else []) + vault_docs

    # 4. Busqueda en vivo (opcional, no rompe si no hay TAVILY_API_KEY)
    live_context = ""
    live_used: list[dict] = []
    if live and config.TAVILY_API_KEY:
        try:
            from research_agent import search_and_scrape
            results = await search_and_scrape(topic, max_results=max_results_live)
            if results:
                live_parts = []
                for r in results:
                    live_parts.append(
                        f"[FUENTE WEB: {r.get('title', '')} — {r.get('url', '')}]\n"
                        f"{r.get('full_text', r.get('snippet', ''))[:3000]}"
                    )
                    live_used.append({"title": r.get("title", ""), "url": r.get("url", "")})
                live_context = "\n\n".join(live_parts)
        except Exception as e:
            console.print(f"[yellow][STUDY] live research fallo: {e}[/yellow]")

    # 5. Construir contexto con leyes completas (usa el max_context del LLM)
    _chat_system, user_prompt, prompt_input = build_context(
        query=topic,
        route=route,
        personal_docs=personal_docs,
        live_context=live_context,
        llm_client=llm,
    )
    try:
        from context_rag.prompt_builder import maybe_pre_summarize
        user_prompt = await maybe_pre_summarize(
            user_prompt=user_prompt,
            query=topic,
            route=route,
            llm_client=llm,
            prompt_input=prompt_input,
        )
    except Exception as e:
        console.print(f"[yellow][STUDY] pre-resumir fallo: {e}[/yellow]")
    system = _study_system_prompt()

    user_prompt += (
        "\n\n=== INSTRUCCION FINAL ===\n"
        f"Redacta el estudio tributario completo sobre: {topic}\n"
        "Sigue la estructura obligatoria del modo estudio. Markdown permitido."
    )

    # 6. Generar
    content = await llm.chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        temperature=STUDY_TEMPERATURE,
        max_tokens=STUDY_MAX_TOKENS,
    )
    content = content.strip()

    # 7. Guardrail de citas contra las fuentes efectivamente entregadas
    try:
        content = guardrail_check(system + "\n" + user_prompt, content)
    except Exception as e:
        console.print(f"[yellow][STUDY] guardrail fallo: {e}[/yellow]")

    # 8. Guardar en el vault
    refs = extract_article_refs(topic + "\n" + content)
    refs_str = [f"Art. {art} {tag.upper()}" for tag, art in refs][:15]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_topic = "".join(c if c.isalnum() else "_" for c in topic.lower())[:50].strip("_")

    folder = f"Clientes/{cliente}/Analisis" if cliente else "Estudios"
    path = write_note(
        folder=folder,
        filename=f"estudio_{safe_topic}_{timestamp}",
        content=content,
        title=f"Estudio: {topic[:80]}",
        tipo="estudio",
        cliente=cliente,
        tags=["estudio", route.query_type] + [t for t in route.law_tags],
        fuentes=refs_str,
    )

    return {
        "topic": topic,
        "cliente": cliente,
        "content": content,
        "path": str(path),
        "refs": refs,
        "docs_usados": docs_used,
        "live_usado": live_used,
        "laws_loaded": prompt_input.route.law_tags,
        "tokens_est": prompt_input.tokens_used,
        "trim_applied": prompt_input.trim_applied,
    }


def run_study_sync(topic: str, **kwargs) -> dict[str, Any]:
    """Wrapper sincrono."""
    import asyncio
    return asyncio.run(run_study(topic, **kwargs))
