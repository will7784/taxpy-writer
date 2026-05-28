"""
Motor de navegación del grafo de conocimiento (GraphRAG).

Permite cruzar leyes, jurisprudencia y circulares navegando las aristas
del grafo en lugar de depender únicamente de la similitud vectorial.
"""

from __future__ import annotations

from rich.console import Console

import config
from models import DocumentChunk, SearchResult
from supabase_client import supabase

console = Console()


class GraphEngine:
    """Navega el grafo de conocimiento en Supabase."""

    def __init__(self) -> None:
        self._table_exists: bool | None = None

    def _check_table(self) -> bool:
        """Verifica si la tabla knowledge_graph existe."""
        if self._table_exists is not None:
            return self._table_exists
        try:
            supabase.table("knowledge_graph").select("id", count="exact").limit(1).execute()
            self._table_exists = True
        except Exception:
            self._table_exists = False
            console.print("[yellow]⚠️ Tabla knowledge_graph no existe. GraphRAG desactivado. "
                          "Ejecuta el SQL en supabase_rag_schema.sql para crearla.[/yellow]")
        return self._table_exists

    async def insert_relations(self, relations: list[dict]) -> int:
        """Inserta relaciones en knowledge_graph, evitando duplicados."""
        if not relations or not self._check_table():
            return 0

        # Deduplicar por (source, target, type)
        seen: set[tuple[str, str, str]] = set()
        unique: list[dict] = []
        for r in relations:
            key = (r["source_chunk_uid"], r["target_chunk_uid"], r["relation_type"])
            if key not in seen:
                seen.add(key)
                unique.append(r)

        if not unique:
            return 0

        try:
            # Upsert evita duplicados
            supabase.table("knowledge_graph").upsert(unique, on_conflict="source_chunk_uid,target_chunk_uid,relation_type").execute()
            return len(unique)
        except Exception as e:
            console.print(f"[yellow]⚠️ Error insertando relaciones: {e}[/yellow]")
            return 0

    def get_neighbors(
        self,
        chunk_uid: str,
        relation_types: list[str] | None = None,
        depth: int = 1,
        max_per_level: int = 5,
    ) -> list[str]:
        """
        Devuelve los chunk_uids vecinos de un nodo en el grafo.

        Args:
            chunk_uid: UID del nodo origen.
            relation_types: Filtrar por tipo de relación (None = todos).
            depth: Profundidad de navegación (1 = vecinos directos, 2 = vecinos de vecinos).
            max_per_level: Máximo de vecinos por nodo por nivel.
        """
        if not self._check_table():
            return []

        result: list[str] = []
        visited: set[str] = {chunk_uid}
        current_level = {chunk_uid}

        for _ in range(depth):
            next_level: set[str] = set()
            for uid in current_level:
                try:
                    query = (
                        supabase.table("knowledge_graph")
                        .select("target_chunk_uid, source_chunk_uid")
                        .or_(f"source_chunk_uid.eq.{uid},target_chunk_uid.eq.{uid}")
                    )
                    if relation_types:
                        query = query.in_("relation_type", relation_types)
                    response = query.limit(max_per_level).execute()

                    for row in response.data:
                        neighbor = row["target_chunk_uid"] if row["source_chunk_uid"] == uid else row["source_chunk_uid"]
                        if neighbor not in visited:
                            visited.add(neighbor)
                            next_level.add(neighbor)
                            result.append(neighbor)
                except Exception as e:
                    console.print(f"[yellow]⚠️ Error navegando grafo desde {uid}: {e}[/yellow]")
                    continue
            current_level = next_level
            if not current_level:
                break

        return result

    def expand_results(
        self,
        chunk_uids: list[str],
        top_n: int = 5,
        relation_types: list[str] | None = None,
    ) -> list[str]:
        """
        Dado un conjunto de chunks encontrados por RAG, trae los más relacionados.

        Prioriza chunks que son vecinos de múltiples resultados (intersección).
        """
        if not self._check_table() or not chunk_uids:
            return []

        neighbor_counts: dict[str, int] = {}
        for uid in chunk_uids:
            neighbors = self.get_neighbors(uid, relation_types=relation_types, depth=1, max_per_level=top_n)
            for n in neighbors:
                if n in chunk_uids:
                    continue  # ya está en los resultados originales
                neighbor_counts[n] = neighbor_counts.get(n, 0) + 1

        # Ordenar por cantidad de conexiones (más conectado = más relevante)
        sorted_neighbors = sorted(neighbor_counts.items(), key=lambda x: x[1], reverse=True)
        return [uid for uid, _ in sorted_neighbors[:top_n]]

    async def get_chunk_by_uid(self, chunk_uid: str) -> DocumentChunk | None:
        """Recupera un chunk desde Supabase por su UID."""
        try:
            response = supabase.table("document_chunks").select("*").eq("chunk_uid", chunk_uid).limit(1).execute()
            if response.data:
                return DocumentChunk.from_db_row(response.data[0])
        except Exception as e:
            console.print(f"[yellow]⚠️ Error cargando chunk {chunk_uid}: {e}[/yellow]")
        return None


# Instancia global
graph = GraphEngine()
