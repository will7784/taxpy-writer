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
from http_security import tls_context
from urllib.parse import urlparse
from rich.console import Console

import config

console = Console()

TAVILY_URL = "https://api.tavily.com/search"
# Un fallo o una actuación oficial no pierde su carácter por no ser tributaria.
# Esta lista sólo clasifica autoridad de la fuente; la pertinencia se revisa
# después con el texto completo.
OFFICIAL_DOMAINS = [
    "bcn.cl", "sii.cl", "uaf.cl", "tta.cl", "pjud.cl", "scj.cl", "cmfchile.cl",
    "fiscaliadechile.cl", "diariooficial.interior.gob.cl",
]  # fallback; la jurisdiccion activa puede sobreescribir

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


class SearchUnavailable(RuntimeError):
    """La búsqueda no pudo ejecutarse; no equivale a cero resultados."""


async def search_live(query: str, *, strict: bool = False) -> list[dict]:
    """Consulta todas las fuentes devueltas por cada búsqueda, sin corte local."""
    if not config.TAVILY_API_KEY:
        if strict:
            raise SearchUnavailable("Falta configurar TAVILY_API_KEY para buscar en la web.")
        return []

    request_size = config.TAVILY_RESULTS_PER_QUERY
    if request_size < 1:
        raise ValueError("TAVILY_RESULTS_PER_QUERY debe ser mayor que cero.")
    official_domains, country = _jurisdiction_settings()
    async with httpx.AsyncClient(timeout=15, verify=tls_context()) as client:
        try:
            results = await _tavily_search(
                client, query, include_domains=official_domains, max_results=request_size, country=country
            )
            # La búsqueda general complementa la oficial aun cuando esta tenga resultados.
            extra = await _tavily_search(client, query, include_domains=None,
                                         max_results=request_size, country=country)
            seen = {r["url"] for r in results}
            for result in extra:
                if result["url"] not in seen:
                    results.append(result)
                    seen.add(result["url"])
            for result in results:
                host = (urlparse(result["url"]).hostname or "").lower()
                result["official"] = any(host == d or host.endswith("." + d) for d in official_domains)
            return results
        except SearchUnavailable:
            if strict:
                raise
            return []


async def _tavily_search(
    client: httpx.AsyncClient,
    query: str,
    *,
    include_domains: list[str] | None,
    max_results: int,
    country: str = "chile",
) -> list[dict]:
    payload: dict = {
        "query": f"{query} {country}" if country and country not in query.lower() else query,
        "search_depth": "basic",
        "max_results": max_results,
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
    except httpx.HTTPStatusError as e:
        raise SearchUnavailable(f"El buscador web rechazó la consulta (HTTP {e.response.status_code}). Revisa la clave y el saldo de Tavily.") from e
    except Exception as e:
        raise SearchUnavailable("No se pudo conectar con el buscador web. Intenta nuevamente.") from e

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
    lines = ["FUENTES WEB EN VIVO (citar URL; las fuentes secundarias son orientación, no acreditan vigencia):"]
    for r in results:
        snippet = r["content"][:500]
        kind = "oficial" if r.get("official") else "secundaria"
        lines.append(f"- [{kind}] {r['title']} ({r['url']}): {snippet}")
    return "\n".join(lines)
