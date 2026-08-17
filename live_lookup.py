"""
Capa 3 del router (telegram_mvp_bot.py:_process_chat): búsqueda en fuentes
vivas cuando el árbol de decisión y el RAG interno no cubren la consulta.

Nota de diseño: se descartaron los conectores a medida contra LeyChile
(BCN) y sii.cl que se habían planteado inicialmente — ninguno de los dos
expone una API pública y documentada para esto. LeyChile no publica un
contrato JSON estable para consulta de artículos (su "web service" no
tiene documentación de terceros verificable); la única API real de
sii.cl requiere registro formal vía Oficina de Partes y es específica
para "Inicio de Actividades" (Ley 21.713), no para normativa/circulares.

En su lugar, se usa Tavily con `include_domains` para priorizar bcn.cl y
sii.cl (fuentes oficiales) y solo cae a búsqueda general sin restricción
de dominio si eso no trae resultados. Requiere TAVILY_API_KEY en config.py;
si no está configurada, la Capa 3 queda desactivada sin romper nada.
"""

from __future__ import annotations

import httpx
from rich.console import Console

import config

console = Console()

TAVILY_URL = "https://api.tavily.com/search"
OFFICIAL_DOMAINS = ["bcn.cl", "sii.cl"]  # fallback; la jurisdiccion activa puede sobreescribir

_COUNTRY_BY_JURISDICTION = {"chile": "chile", "colombia": "colombia"}


def _jurisdiction_settings() -> tuple[list[str], str]:
    """Dominios oficiales y pais segun la jurisdiccion activa (jurisdictions/)."""
    try:
        from jurisdictions import get_jurisdiction
        j = get_jurisdiction()
        domains = j.official_domains or OFFICIAL_DOMAINS
        country = _COUNTRY_BY_JURISDICTION.get(j.code, "chile")
        return domains, country
    except Exception:
        return OFFICIAL_DOMAINS, "chile"


async def search_live(query: str, *, max_results: int = 5) -> list[dict]:
    """Busca en fuentes vivas. Prioriza dominios oficiales; cae a web general si no hay resultados."""
    if not config.TAVILY_API_KEY:
        return []

    official_domains, country = _jurisdiction_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        official = await _tavily_search(
            client, query, include_domains=official_domains, max_results=max_results, country=country
        )
        if official:
            return official
        return await _tavily_search(client, query, include_domains=None, max_results=max_results, country=country)


async def _tavily_search(
    client: httpx.AsyncClient,
    query: str,
    *,
    include_domains: list[str] | None,
    max_results: int,
    country: str = "chile",
) -> list[dict]:
    payload: dict = {
        "query": query,
        "search_depth": "basic",
        "max_results": max_results,
        "country": country,
        "include_answer": False,
    }
    if include_domains:
        payload["include_domains"] = include_domains

    try:
        resp = await client.post(
            TAVILY_URL,
            json=payload,
            headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        console.print(f"[yellow][LIVE_LOOKUP] Tavily falló: {e}[/yellow]")
        return []

    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
        }
        for r in data.get("results", [])
    ]


def format_for_context(results: list[dict]) -> str:
    """Formatea resultados de búsqueda en vivo para inyectar como contexto adicional al LLM."""
    if not results:
        return ""
    lines = ["FUENTES WEB EN VIVO (citar con la URL entre paréntesis, verificar vigencia):"]
    for r in results:
        snippet = r["content"][:500]
        lines.append(f"- {r['title']} ({r['url']}): {snippet}")
    return "\n".join(lines)
