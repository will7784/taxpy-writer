"""
Re-chunk quirurgico de articulos criticos de leyes chilenas.

Usa BATCHING para embeddings e inserciones Supabase.
"""

import asyncio
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import AsyncOpenAI

import config
from supabase_client import supabase
from extract_clean_articles import (
    extract_article_from_pdf,
    extract_article_14_letters,
    extract_article_17_n8,
    chunk_text,
)


CRITICAL_ARTICLES = {
    "documents/DL-824_31-DIC-1974.pdf": {
        "law_tag": "lir",
        "articles": ["20", "21", "31", "41", "54", "96"],
        "special": {
            "14": extract_article_14_letters,
            "17_n8": extract_article_17_n8,
        },
    },
    "documents/DL-830_31-DIC-1974_codigo tributario.pdf": {
        "law_tag": "codigo_tributario",
        "articles": ["8", "59", "60", "61", "97", "120", "121", "122", "192"],
        "special": {},
    },
    "documents/DL-825_31-DIC-1974.pdf": {
        "law_tag": "iva",
        "articles": ["1", "2", "3", "12", "14", "15", "23"],
        "special": {},
    },
}


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Genera embeddings para multiples textos en una sola llamada."""
    if not texts:
        return []
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    response = await client.embeddings.create(
        model=config.OPENAI_EMBEDDING_MODEL,
        input=texts,
    )
    return [d.embedding for d in response.data]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def make_uid(law_tag: str, art_key: str, idx: int) -> str:
    base = f"ley_{law_tag}_art_{art_key}"
    return f"{base}_{idx}" if idx > 0 else base


async def process_pdf(pdf_path: str, law_tag: str, articles: list[str], special: dict):
    print(f"\n[RECHUNK] {pdf_path} ({law_tag})")

    # ── Extraer todos los articulos ──
    extracted: dict[str, str] = {}
    for key, extractor in special.items():
        print(f"  Art. {key} (especial)...")
        result = extractor(pdf_path)
        if isinstance(result, dict):
            extracted.update(result)
        elif isinstance(result, str):
            extracted[key] = result

    for art in articles:
        if art in extracted:
            continue
        print(f"  Art. {art}...")
        text = extract_article_from_pdf(pdf_path, art)
        if text:
            extracted[art] = text

    print(f"  Extraidos: {len(extracted)}")

    # ── Chunk todos los articulos ──
    all_chunks: list[dict] = []  # {uid, text, art_key, idx, total}
    for art_key, text in extracted.items():
        chunks = chunk_text(text, max_chars=3500)
        print(f"  Art. {art_key}: {len(text)} chars -> {len(chunks)} chunk(s)")
        for idx, chunk_text_content in enumerate(chunks):
            uid = make_uid(law_tag, art_key, idx)
            all_chunks.append({
                "uid": uid,
                "text": chunk_text_content,
                "art_key": art_key,
                "idx": idx,
                "total": len(chunks),
            })

    if not all_chunks:
        return 0

    # ── Generar embeddings en BATCH ──
    print(f"  Generando {len(all_chunks)} embeddings (batch)...")
    texts = [c["text"] for c in all_chunks]
    embeddings = await embed_batch(texts)
    for c, emb in zip(all_chunks, embeddings):
        c["embedding"] = emb

    # ── Eliminar chunks existentes ──
    uids = [c["uid"] for c in all_chunks]
    print(f"  Eliminando {len(uids)} chunks antiguos...")
    for uid in uids:
        try:
            supabase.table("document_chunks").delete().eq("chunk_uid", uid).execute()
        except Exception:
            pass

    # ── Insertar en Supabase en BATCH ──
    print(f"  Insertando {len(all_chunks)} chunks nuevos...")
    rows = []
    for c in all_chunks:
        rows.append({
            "chunk_uid": c["uid"],
            "source_path": pdf_path,
            "filename": Path(pdf_path).name,
            "source_type": "ley",
            "law_tag": law_tag,
            "section_level_name": f"ART. {c['art_key'].upper()} {law_tag.upper()}",
            "content": c["text"],
            "metadata": {
                "article": c["uid"],
                "rechunked": True,
                "date": "2026-06-05",
                "clean": True,
            },
            "content_hash": content_hash(c["text"]),
            "is_derogada": False,
            "embedding": c["embedding"],
            "chunk_index": c["idx"],
            "total_chunks": c["total"],
        })

    # Supabase insert batch (max 1000 por llamada)
    BATCH_SIZE = 500
    inserted = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        try:
            supabase.table("document_chunks").insert(batch).execute()
            inserted += len(batch)
            print(f"    Batch {i//BATCH_SIZE + 1}/{(len(rows)-1)//BATCH_SIZE + 1}: +{len(batch)}")
        except Exception as e:
            print(f"    [ERROR] Batch {i//BATCH_SIZE + 1}: {e}")

    print(f"  Insertados: {inserted}")
    return inserted


async def main():
    print("=" * 60)
    print("RE-CHUNK QUIRURGICO - BATCH MODE")
    print("=" * 60)

    total = 0
    for pdf, spec in CRITICAL_ARTICLES.items():
        n = await process_pdf(pdf, spec["law_tag"], spec["articles"], spec.get("special", {}))
        total += n

    print(f"\n{'=' * 60}")
    print(f"COMPLETADO: {total} chunks insertados")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
