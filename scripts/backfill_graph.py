"""
Backfill del grafo de conocimiento para chunks existentes.

Ejecutar UNA VEZ después de crear la tabla knowledge_graph en Supabase.
Procesa todos los chunks existentes, extrae relaciones con GPT-4o-mini,
y las inserta en knowledge_graph.

Uso:
    python scripts/backfill_graph.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from critical_relations import get_critical_relations
from graph_engine import graph as graph_engine
from graph_extractor import GraphExtractor
from models import DocumentChunk
from supabase_client import supabase


BATCH_SIZE = 15


async def main() -> None:
    print("🕸️  Backfill del grafo de conocimiento")
    print("=" * 50)

    # 1. Insertar relaciones críticas primero
    print("\n1️⃣ Insertando relaciones críticas hardcodeadas...")
    critical = get_critical_relations()
    inserted = await graph_engine.insert_relations(critical)
    print(f"   ✓ {inserted} relaciones críticas insertadas")

    # 2. Cargar todos los chunks existentes
    print("\n2️⃣ Cargando chunks existentes...")
    response = supabase.table("document_chunks").select("*").execute()
    rows = response.data
    print(f"   📚 {len(rows)} chunks encontrados")

    # 3. Procesar en batches
    extractor = GraphExtractor()
    total_relations = 0

    for i in range(0, len(rows), BATCH_SIZE):
        batch_rows = rows[i : i + BATCH_SIZE]
        chunks = [DocumentChunk.from_db_row(r) for r in batch_rows]

        print(f"\n   🔄 Batch {i // BATCH_SIZE + 1}/{(len(rows) + BATCH_SIZE - 1) // BATCH_SIZE} "
              f"({len(chunks)} chunks)")

        relations = await extractor.extract_relations_batch(chunks)
        if relations:
            inserted = await graph_engine.insert_relations(relations)
            total_relations += inserted
            print(f"   ✓ {inserted} relaciones insertadas (total: {total_relations})")
        else:
            print(f"   ⏭️  Sin relaciones nuevas")

    print("\n" + "=" * 50)
    print(f"✅ Backfill completado: {total_relations} relaciones totales en knowledge_graph")


if __name__ == "__main__":
    asyncio.run(main())
