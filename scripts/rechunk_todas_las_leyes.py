"""
Fase 2: Re-chunk completo de TODAS las leyes (DL-824, DL-825, DL-830).

Elimina todos los chunks de leyes existentes e inserta articulos limpios,
divididos en chunks coherentes con oraciones completas.
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
from extract_clean_articles import clean_text, chunk_text


PDFS = {
    "documents/DL-824_31-DIC-1974.pdf": {
        "law_tag": "lir",
        "name": "Ley de Impuesto a la Renta (DL-824)",
    },
    "documents/DL-830_31-DIC-1974_codigo tributario.pdf": {
        "law_tag": "codigo_tributario",
        "name": "Codigo Tributario (DL-830)",
    },
    "documents/DL-825_31-DIC-1974.pdf": {
        "law_tag": "iva",
        "name": "Ley del IVA (DL-825)",
    },
}


def find_all_articles(text: str) -> list[str]:
    """Encuentra todos los numeros de articulo en un PDF."""
    patterns = [
        r'ARTICULO\s+(\d+[\w°\u00ba]*)\.?\s*-',
        r'Art\u00edculo\s+(\d+[\w°\u00ba]*)\.?\s*-',
    ]
    articles = set()
    for pat in patterns:
        for m in re.finditer(pat, text):
            num = m.group(1).replace("°", "").replace("\u00ba", "")
            if num.isdigit():
                articles.add(int(num))
    return sorted(articles)


def extract_article(text: str, article_num: int) -> str | None:
    """Extrae un articulo del texto completo de un PDF."""
    patterns = [
        rf'ARTICULO\s+{article_num}[°\u00ba]?\.?\s*-',
        rf'Art\u00edculo\s+{article_num}[°\u00ba]?\.?\s*-',
    ]

    start = -1
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            start = m.start()
            break

    if start < 0:
        return None

    # Buscar siguiente articulo
    end = len(text)
    next_pats = [
        r'\n\s*ARTICULO\s+\d+',
        r'\n\s*Art\u00edculo\s+\d+',
    ]
    for next_pat in next_pats:
        m = re.search(next_pat, text[start + 30:])
        if m:
            candidate = start + 30 + m.start()
            if candidate < end:
                end = candidate

    raw = text[start:end]
    cleaned = clean_text(raw)
    return cleaned if cleaned else None


async def embed_batch(texts: list[str]) -> list[list[float]]:
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


def make_uid(law_tag: str, art_num: int, idx: int) -> str:
    base = f"ley_{law_tag}_art_{art_num}"
    return f"{base}_{idx}" if idx > 0 else base


async def process_pdf(pdf_path: str, law_tag: str, name: str):
    print(f"\n{'='*60}")
    print(f"[RECHUNK] {name}")
    print(f"{'='*60}")

    # Cargar PDF
    import fitz
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"
    doc.close()

    # Encontrar todos los articulos
    article_nums = find_all_articles(full_text)
    print(f"  Articulos encontrados: {len(article_nums)} ({article_nums[0]} - {article_nums[-1]})")

    # Extraer todos los articulos
    all_chunks: list[dict] = []
    missing = []

    for art_num in article_nums:
        text = extract_article(full_text, art_num)
        if not text:
            missing.append(art_num)
            continue

        chunks = chunk_text(text, max_chars=3500)
        for idx, chunk_text_content in enumerate(chunks):
            uid = make_uid(law_tag, art_num, idx)
            all_chunks.append({
                "uid": uid,
                "text": chunk_text_content,
                "art_num": art_num,
                "idx": idx,
                "total": len(chunks),
            })

    if missing:
        print(f"  [WARN] No encontrados: {missing}")

    print(f"  Total chunks a generar: {len(all_chunks)}")

    if not all_chunks:
        return 0

    # Generar embeddings en batch
    print(f"  Generando embeddings (batch)...")
    texts = [c["text"] for c in all_chunks]
    embeddings = await embed_batch(texts)
    for c, emb in zip(all_chunks, embeddings):
        c["embedding"] = emb

    # Eliminar TODOS los chunks existentes de esta ley
    print(f"  Eliminando chunks antiguos de {law_tag}...")
    try:
        supabase.table("document_chunks").delete().eq("source_type", "ley").eq("law_tag", law_tag).execute()
        print(f"    Eliminados")
    except Exception as e:
        print(f"    [WARN] {e}")

    # Insertar en batch
    print(f"  Insertando {len(all_chunks)} chunks...")
    rows = []
    for c in all_chunks:
        rows.append({
            "chunk_uid": c["uid"],
            "source_path": pdf_path,
            "filename": Path(pdf_path).name,
            "source_type": "ley",
            "law_tag": law_tag,
            "section_level_name": f"ART. {c['art_num']} {law_tag.upper()}",
            "content": c["text"],
            "metadata": {
                "article": c["uid"],
                "rechunked": True,
                "date": "2026-06-05",
                "clean": True,
                "fase2": True,
            },
            "content_hash": content_hash(c["text"]),
            "is_derogada": False,
            "embedding": c["embedding"],
            "chunk_index": c["idx"],
            "total_chunks": c["total"],
        })

    BATCH_SIZE = 500
    inserted = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        try:
            supabase.table("document_chunks").insert(batch).execute()
            inserted += len(batch)
            print(f"    Batch {i//BATCH_SIZE + 1}: +{len(batch)}")
        except Exception as e:
            print(f"    [ERROR] Batch {i//BATCH_SIZE + 1}: {e}")

    print(f"  Insertados: {inserted}")
    return inserted


async def main():
    print("=" * 60)
    print("FASE 2: RE-CHUNK COMPLETO DE LEYES")
    print("=" * 60)

    total = 0
    for pdf_path, spec in PDFS.items():
        n = await process_pdf(pdf_path, spec["law_tag"], spec["name"])
        total += n

    print(f"\n{'=' * 60}")
    print(f"COMPLETADO: {total} chunks insertados")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
