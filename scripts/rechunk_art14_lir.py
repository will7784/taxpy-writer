"""
Re-chunking manual del Art. 14 del LIR (DL-824).

El parser original no separó las letras A, B, C, D, E del Art. 14,
por lo que el contenido quedó perdido o en un solo chunk gigante
truncado (ley_lir_art_14 con solo 266 chars).

Este script:
1. Lee el texto extraído manualmente del PDF
2. Separa por letras A, B, C, D, E
3. Divide secciones largas en sub-chunks de ~8.000 chars
4. Genera embeddings
5. Borra el chunk viejo
6. Inserta los nuevos chunks con UIDs específicos
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import DocumentChunk
from ingest import EmbeddingGenerator
from supabase_client import supabase
import config


MAX_CHUNK_SIZE = 8000
OVERLAP = 500


def split_text(text: str, max_size: int, overlap: int) -> list[str]:
    """Divide texto en chunks con overlap, respetando párrafos."""
    if len(text) <= max_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + max_size
        if end >= len(text):
            chunks.append(text[start:])
            break

        # Buscar el último salto de línea antes del límite
        search_text = text[start:end]
        last_nl = search_text.rfind("\n")
        if last_nl > max_size * 0.7:
            end = start + last_nl + 1

        chunks.append(text[start:end])
        start = end - overlap

    return chunks


def parse_sections(text: str) -> dict[str, str]:
    """Separa el Art. 14 en intro + letras A-E."""
    lines = text.split("\n")

    # Encontrar índices de las letras principales
    letter_indices = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^[A-E]\)", stripped):
            if any(k in stripped for k in ["Rentas", "Régimen", "Efectos", "Incentivo", "Micro", "Pequeñas", "Transparencia"]):
                letter_indices.append((i, stripped[0]))

    sections: dict[str, str] = {}

    # Intro (antes de la primera letra)
    if letter_indices:
        intro_lines = lines[:letter_indices[0][0]]
    else:
        intro_lines = lines
    sections["intro"] = "\n".join(intro_lines)

    # Cada letra
    for idx, (line_idx, letter) in enumerate(letter_indices):
        start = line_idx
        end = letter_indices[idx + 1][0] if idx + 1 < len(letter_indices) else len(lines)
        sections[letter.lower()] = "\n".join(lines[start:end])

    return sections


async def main() -> None:
    raw_path = Path("art_14_only.txt")
    if not raw_path.exists():
        print("❌ No se encontró art_14_only.txt. Ejecuta primero la extracción del PDF.")
        return

    text = raw_path.read_text(encoding="utf-8")
    sections = parse_sections(text)

    print(f"Secciones encontradas: {list(sections.keys())}")
    for k, v in sections.items():
        print(f"  {k}: {len(v)} chars")

    # Crear DocumentChunks
    chunks: list[DocumentChunk] = []
    law_tag = "lir"
    filename = "DL-824_31-DIC-1974.pdf"
    source_path = str(config.DOCUMENTS_DIR / filename)

    for section_key, section_text in sections.items():
        if not section_text.strip():
            continue

        sub_chunks = split_text(section_text, MAX_CHUNK_SIZE, OVERLAP)
        for i, sub_text in enumerate(sub_chunks):
            if len(sub_chunks) == 1:
                uid = f"ley_{law_tag}_art_14_{section_key}"
            else:
                uid = f"ley_{law_tag}_art_14_{section_key}_{i}"

            header = f"ARTÍCULO 14°"
            if section_key != "intro":
                header += f" letra {section_key.upper()})"
            if len(sub_chunks) > 1:
                header += f" (parte {i + 1}/{len(sub_chunks)})"

            chunk = DocumentChunk(
                chunk_uid=uid,
                source_path=source_path,
                filename=filename,
                source_type="ley",
                law_tag=law_tag,
                hierarchy_path=law_tag,
                section_level_name=header,
                content=sub_text.strip(),
                content_hash=hashlib.sha256(sub_text.encode()).hexdigest(),
                metadata={"tipo": "ley", "filename": filename, "rechunked": True},
            )
            chunks.append(chunk)

    print(f"\nTotal de chunks a insertar: {len(chunks)}")
    for c in chunks:
        print(f"  {c.chunk_uid}: {len(c.content)} chars")

    # Borrar chunk viejo
    print("\n🗑️  Borrando chunk viejo ley_lir_art_14...")
    try:
        supabase.table("document_chunks").delete().eq("chunk_uid", "ley_lir_art_14").execute()
        print("  ✓ Chunk viejo eliminado")
    except Exception as e:
        print(f"  ⚠️ Error borrando chunk viejo (puede que no exista): {e}")

    # Generar embeddings e insertar
    embedder = EmbeddingGenerator()
    batch_size = 10

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        texts = [c.content for c in batch]

        print(f"\n🔄 Generando embeddings batch {i // batch_size + 1} ({len(batch)} chunks)...")
        try:
            embeddings = await embedder.generate(texts)
        except Exception as e:
            print(f"  ⚠️ Error en batch, intentando uno por uno: {e}")
            embeddings = []
            for c in batch:
                try:
                    emb = await embedder.generate([c.content])
                    embeddings.append(emb[0])
                except Exception as e2:
                    print(f"  ✗ Error en {c.chunk_uid}: {e2}")
                    embeddings.append(None)

        records = []
        for chunk, emb in zip(batch, embeddings):
            if emb is None or len(emb) != 1536:
                print(f"  ⚠️ Embedding inválido para {chunk.chunk_uid}, saltando")
                continue
            record = chunk.to_db_dict()
            record["embedding"] = emb
            records.append(record)

        if not records:
            continue

        try:
            result = supabase.table("document_chunks").upsert(records, on_conflict="chunk_uid").execute()
            print(f"  ✓ Insertados {len(records)} chunks")
        except Exception as e:
            print(f"  ✗ Error insertando: {e}")

    print("\n✅ Re-chunking del Art. 14 LIR completado.")


if __name__ == "__main__":
    asyncio.run(main())
