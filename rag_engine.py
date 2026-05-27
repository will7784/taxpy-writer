"""
Motor de búsqueda semántica (RAG) usando Supabase pgvector.

Reemplaza a NotebookLM como backend de recuperación de información.
"""

from __future__ import annotations

import re
from typing import Any

from openai import AsyncOpenAI
from rich.console import Console

import config
from models import DocumentChunk, SearchResult
from supabase_client import supabase

console = Console()


class RAGEngine:
    """
    Motor de Recuperación Augmentada por Generación (RAG).

    Flujo:
    1. Recibe una pregunta del usuario
    2. Genera embedding de la pregunta
    3. Busca los chunks más similares en Supabase pgvector
    4. Opcionalmente re-rankea con GPT-4o-mini
    5. Retorna contexto estructurado para el LLM
    """

    DEFAULT_TOP_K = 10
    CONVERSATION_TOP_K = 5

    def __init__(self) -> None:
        self._openai = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

    async def search(
        self,
        query: str,
        organization_id: str | None = None,
        source_types: list[str] | None = None,
        law_tags: list[str] | None = None,
        top_k: int | None = None,
        include_derogadas: bool = False,
    ) -> list[SearchResult]:
        """
        Busca chunks relevantes para una consulta.

        Args:
            query: Pregunta o tema de búsqueda
            organization_id: UUID de la organización (None = solo docs públicos)
            source_types: Filtrar por tipo de fuente (ley, circular, jurisprudencia_judicial, etc.)
            law_tags: Filtrar por ley (lir, iva, codigo_tributario)
            top_k: Cantidad de resultados (default 10, max 50)
            include_derogadas: Incluir normas derogadas
        """
        top_k = top_k or self.DEFAULT_TOP_K
        top_k = max(1, min(top_k, 50))

        # 1. Generar embedding de la query
        embedding = await self._embed_query(query)

        # 2. Construir filtros
        # Si hay múltiples source_types o law_tags, hacemos múltiples búsquedas
        # y mergeamos resultados (PostgREST RPC no soporta IN directamente en una sola llamada)
        all_results: list[SearchResult] = []

        source_types_list = source_types or [None]
        law_tags_list = law_tags or [None]

        for st in source_types_list:
            for lt in law_tags_list:
                params = {
                    "query_embedding": embedding,
                    "match_count": top_k,
                    "filter_source_type": st,
                    "filter_law_tag": lt,
                    "include_derogadas": include_derogadas,
                    "filter_organization_id": organization_id,
                }

                try:
                    rpc = await supabase.rpc("match_document_chunks", params)
                    response = rpc.execute()

                    if response.data:
                        for row in response.data:
                            chunk = DocumentChunk.from_db_row(row)
                            similarity = row.get("similarity", 0.0)
                            all_results.append(SearchResult(chunk=chunk, similarity=similarity))
                except Exception as e:
                    console.print(f"[red]❌ Error en búsqueda RAG: {e}[/red]")

        # 3. Deduplicar por chunk_uid y ordenar por similarity
        seen: set[str] = set()
        unique_results: list[SearchResult] = []
        for r in sorted(all_results, key=lambda x: x.similarity, reverse=True):
            if r.chunk.chunk_uid not in seen:
                seen.add(r.chunk.chunk_uid)
                unique_results.append(r)

        return unique_results[:top_k]

    async def search_for_conversation(
        self,
        query: str,
        organization_id: str | None = None,
    ) -> list[SearchResult]:
        """Búsqueda optimizada para modo conversación (top 5, todas las fuentes)."""
        return await self.search(
            query=query,
            organization_id=organization_id,
            top_k=self.CONVERSATION_TOP_K,
            include_derogadas=False,
        )

    async def search_for_document(
        self,
        query: str,
        content_type: str,
        organization_id: str | None = None,
    ) -> list[SearchResult]:
        """
        Búsqueda optimizada para generación de documentos largos.

        Según el tipo de contenido, prioriza ciertas fuentes.
        """
        # Mapeo de content_type a source_types prioritarios
        source_type_map: dict[str, list[str]] = {
            "manual": ["ley", "circular", "jurisprudencia_judicial"],
            "articulo": ["ley", "circular", "jurisprudencia_judicial"],
            "guion": ["ley", "circular", "jurisprudencia_judicial"],
            "historia": ["ley", "jurisprudencia_judicial"],
            "conversacion": ["ley", "circular", "jurisprudencia_judicial", "oficio", "resolucion"],
        }

        source_types = source_type_map.get(content_type, ["ley", "circular", "jurisprudencia_judicial"])

        return await self.search(
            query=query,
            organization_id=organization_id,
            source_types=source_types,
            top_k=self.DEFAULT_TOP_K,
            include_derogadas=False,
        )

    async def build_context(self, results: list[SearchResult]) -> str:
        """
        Construye un string de contexto a partir de los resultados de búsqueda,
        formateado para ser usado como contexto en prompts de GPT-4o.
        """
        if not results:
            return "No se encontraron fuentes relevantes en la base de conocimiento."

        lines: list[str] = []
        lines.append("=== FUENTES RELEVANTES ===\n")

        for i, r in enumerate(results, 1):
            chunk = r.chunk
            meta = chunk.metadata or {}

            # Header con fuente y score
            header_parts = [f"[{i}]"]
            if chunk.source_type:
                header_parts.append(chunk.source_type.replace("_", " ").title())
            if chunk.section_level_name:
                header_parts.append(f"— {chunk.section_level_name}")
            if meta.get("codigo_pronunciamiento"):
                header_parts.append(f"({meta['codigo_pronunciamiento']})")

            lines.append(" | ".join(header_parts))

            # Contenido
            content = chunk.content.strip()
            # Limpiar exceso de saltos de línea
            content = re.sub(r"\n{3,}", "\n\n", content)
            lines.append(content)

            # Metadatos adicionales relevantes
            meta_parts = []
            if meta.get("fecha"):
                meta_parts.append(f"Fecha: {meta['fecha']}")
            if meta.get("instancia"):
                meta_parts.append(f"Instancia: {meta['instancia']}")
            if meta.get("pdf_url") and meta["pdf_url"] != "N/A":
                meta_parts.append(f"PDF: {meta['pdf_url']}")

            if meta_parts:
                lines.append(f"  → {' | '.join(meta_parts)}")

            lines.append("")  # separador

        return "\n".join(lines)

    async def _embed_query(self, query: str) -> list[float]:
        """Genera embedding para una query de búsqueda."""
        response = await self._openai.embeddings.create(
            model=config.OPENAI_EMBEDDING_MODEL,
            input=[query],
        )
        return response.data[0].embedding


# Instancia global para uso en toda la aplicación
rag = RAGEngine()
