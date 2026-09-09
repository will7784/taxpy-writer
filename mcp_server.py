"""
Gateway MCP para Impuestia.

Expone las herramientas del backend (Co-Work, jurisprudencia, normas, notas,
investigación y redacción) a agentes externos vía el Model Context Protocol.
Esto es lo que permite que el harness de DeepSeek (u otro cliente MCP) se
conecte a tu FastAPI y use todo el corpus curado como "capa inteligente".

Endpoint SSE: ``{mount_path}/sse``  (por defecto ``/mcp/sse``).
Con ``@deepseek-ai/dsh-mcp-client`` (harness DeepSeek):

    plugins:
      - name: "@deepseek-ai/dsh-mcp-client"
        config:
          transport: "http"
          serverName: "impuestia"
          url: "http://localhost:8000/mcp/sse"

Auth: middleware ASGI que exige ``Authorization: Bearer <MCP_TOKEN>`` en todas
las peticiones al montaje. Si ``MCP_TOKEN`` está vacío (solo dev) no se exige.
Se usa bearer simple, no OAuth 2.1 (que sigue en borrador IETF), pensado para un
despliegue privado/single-tenant.
"""

from __future__ import annotations

import hmac
import re
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

import config

# ── Instancia del servidor MCP ────────────────────────────────────
mcp = FastMCP(
    "Impuestia",
    instructions=(
        "Asistente jurídico tributario chileno. Tienes acceso a la biblioteca "
        "jurídica, jurisprudencia, notas curadas del asesor y a los Co-Work por "
        "cliente. Usa listar_clientes/listar_casos para orientarte, procesar_caso "
        "para ingerir documentos de un cliente, buscar_jurisprudencia/obtener_norma "
        "para fundamentar, investigar para el flujo completo con respaldo y "
        "escribir_informe para dejar el resultado en el vault."
    ),
)


# ── Utilerías de renderizado ───────────────────────────────────────
def _render_trabajo(trabajo: Any) -> str:
    """Renderiza un Trabajo de cowork_manager en texto legible para el LLM."""
    get = getattr(trabajo, "id", None)
    estado = getattr(trabajo, "estado", "") or getattr(trabajo, "status", "")
    titulo = getattr(trabajo, "titulo", "") or ""
    tipo = getattr(trabajo, "tipo", "") or ""
    salida = getattr(trabajo, "archivo_salida", "") or ""
    entrada = getattr(trabajo, "archivo_entrada", "") or ""
    line = f"- [{estado}] {titulo} (tipo: {tipo}, id: {get})"
    if entrada:
        line += f"\n    entrada: {entrada}"
    if salida:
        line += f"\n    salida: {salida}"
    return line


def _render_doc(doc: Any) -> str:
    """Renderiza un VaultDoc de article_index con su snippet."""
    title = getattr(doc, "title", "")
    tipo = getattr(doc, "tipo", "")
    cliente = getattr(doc, "cliente", "")
    aprobada = getattr(doc, "aprobada", False)
    refs = getattr(doc, "refs", []) or []
    try:
        doc.load_text()
    except Exception:
        pass
    snippet = ""
    try:
        snippet = doc.snippet
    except Exception:
        snippet = ""
    parts = [f"### {title}"]
    meta = []
    if tipo:
        meta.append(f"tipo: {tipo}")
    if cliente:
        meta.append(f"cliente: {cliente}")
    if aprobada:
        meta.append("APROBADA")
    if meta:
        parts.append("(" + ", ".join(meta) + ")")
    if refs:
        parts.append("Referencias: " + "; ".join(f"{a} ({b})" for a, b in refs[:6]))
    if snippet:
        parts.append(snippet)
    return "\n".join(parts)


# ── Herramientas (tools) expuestas al agente ────────────────────────
@mcp.tool()
def listar_clientes() -> str:
    """Lista los clientes configurados en el Co-Work."""
    from obsidian_writer import list_clientes
    try:
        items = list_clientes()
    except Exception as exc:
        return f"Error al listar clientes: {exc}"
    if not items:
        return "No hay clientes configurados."
    return "Clientes disponibles: " + ", ".join(
        (item.get("nombre") if isinstance(item, dict) else str(item)) or str(item)
        for item in items
    )


@mcp.tool()
def listar_casos(cliente: str) -> str:
    """Lista los trabajos/expedientes (casos) de un cliente."""
    from cowork_manager import list_trabajos
    try:
        trabajos = list_trabajos(cliente)
    except Exception as exc:
        return f"Error al listar casos de '{cliente}': {exc}"
    if not trabajos:
        return f"No hay trabajos para el cliente '{cliente}'."
    return "\n".join(_render_trabajo(t) for t in trabajos)


@mcp.tool()
async def procesar_caso(cliente: str) -> str:
    """Procesa los documentos de la carpeta entrada/ de un cliente (OCR + análisis)."""
    from cowork_manager import procesar_entrada_cliente
    from llm_client import LLMClient
    try:
        llm = LLMClient()
        trabajos = await procesar_entrada_cliente(cliente, llm_client=llm)
    except Exception as exc:
        return f"Error al procesar el caso de '{cliente}': {exc}"
    if not trabajos:
        return f"No había documentos nuevos en entrada/ para '{cliente}'."
    return "\n".join(_render_trabajo(t) for t in trabajos)


@mcp.tool()
def buscar_jurisprudencia(consulta: str, max_docs: int = 6) -> str:
    """Busca jurisprudencia/notas en el vault por artículo o keywords (índice de citas)."""
    from article_index import article_index
    try:
        docs = article_index.search(consulta, max_docs=max_docs)
    except Exception as exc:
        return f"Error al buscar '{consulta}': {exc}"
    if not docs:
        return f"No se encontraron documentos para: {consulta}"
    return "\n\n".join(_render_doc(d) for d in docs)


@mcp.tool()
def buscar_notas(consulta: str, max_docs: int = 5) -> str:
    """Busca solo notas aprobadas (conocimiento validado por el usuario)."""
    from article_index import article_index
    try:
        docs = article_index.search(consulta, max_docs=max_docs, aprobadas_only=True)
    except Exception as exc:
        return f"Error al buscar notas '{consulta}': {exc}"
    if not docs:
        return f"No se encontraron notas aprobadas para: {consulta}"
    return "\n\n".join(_render_doc(d) for d in docs)


@mcp.tool()
def obtener_norma(clave: str) -> str:
    """Entrega la entrada del catálogo de biblioteca jurídica para una clave (lir, iva, ct, etc.)."""
    from legal_library import catalog_entries
    clave = clave.strip().lower()
    for e in catalog_entries():
        if e.get("key") == clave:
            return "\n".join(f"{k}: {v}" for k, v in e.items())
    keys = [e.get("key") for e in catalog_entries()]
    return f"No se encontró la clave '{clave}'. Claves válidas: " + ", ".join(keys)


@mcp.tool()
async def investigar(consulta: str, cliente: str = "", fecha_hechos: str = "") -> str:
    """Ejecuta el flujo completo de investigación (buscador + biblioteca + jurisprudencia)."""
    from research_agent import run_research
    try:
        result = await run_research(
            consulta,
            cliente=cliente or None,
            fecha_hechos=fecha_hechos or None,
        )
    except Exception as exc:
        return f"Error en la investigación de '{consulta}': {exc}"
    if result.get("reply"):
        return str(result["reply"])
    return str(result.get("resumen") or result.get("warnings") or "Sin resultados.")


@mcp.tool()
def escribir_informe(cliente: str, titulo: str, contenido: str) -> str:
    """Escribe una nota/informe en el vault del cliente y devuelve la ruta."""
    from obsidian_writer import write_note
    slug = re.sub(r"[^a-z0-9]+", "_", titulo.lower()).strip("_") or "informe"
    try:
        path = write_note(
            folder=f"Clientes/{cliente}/Informes",
            filename=f"{slug}_{datetime.now().strftime('%Y%m%d')}",
            content=contenido,
            title=titulo,
            tipo="analisis",
            cliente=cliente,
        )
        return f"Informe guardado en: {path}"
    except Exception as exc:
        return f"Error al escribir el informe: {exc}"


# ── Autenticación (bearer token) ───────────────────────────────────
class _BearerAuthMiddleware:
    """ASGI middleware que exige ``Authorization: Bearer <MCP_TOKEN>``."""

    def __init__(self, app: Any, *, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if self.token and scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            raw = headers.get(b"authorization", b"").decode("latin-1")
            provided = raw[len("Bearer "):] if raw.lower().startswith("bearer ") else ""
            if not hmac.compare_digest(provided, self.token):
                body = b'{"error":"No autorizado"}'
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def build_mcp_app() -> tuple[str, Any]:
    """Retorna ``(mount_path, asgi_app)`` listos para ``app.mount(path, app)``."""
    token = getattr(config, "MCP_TOKEN", "")
    mount_path = getattr(config, "MCP_MOUNT_PATH", "/mcp") or "/mcp"
    inner = mcp.sse_app()
    if token:
        inner = _BearerAuthMiddleware(inner, token=token)
    return mount_path, inner
