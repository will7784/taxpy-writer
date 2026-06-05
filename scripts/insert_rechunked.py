"""
Inserta chunks re-chunkificados del Art. 14 E y Art. 17 N° 8 en Supabase.
"""
import asyncio
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import AsyncOpenAI
import config
from supabase_client import supabase


async def embed_text(text: str) -> list[float]:
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    response = await client.embeddings.create(
        model=config.OPENAI_EMBEDDING_MODEL,
        input=[text],
    )
    return response.data[0].embedding


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


async def main():
    # Leer textos extraidos
    with open('art_17_n8_clean.txt', 'r', encoding='latin-1') as f:
        lines = f.readlines()
    art17_n8 = ''.join(lines[1:]).strip()

    with open('art_14_e_raw.txt', 'r', encoding='latin-1') as f:
        lines = f.readlines()
    art14_e = ''.join(lines[1:]).strip()

    chunks = [
        {
            'uid': 'ley_lir_art_14_e_0',
            'content': art14_e,
            'section': 'Art. 14 E - Incentivo al ahorro (microempresas)',
        },
        {
            'uid': 'ley_lir_art_17_n8',
            'content': art17_n8,
            'section': 'Art. 17 N 8 - Enajenacion de bienes raices (8.000 UF exenta)',
        },
    ]

    # Eliminar chunks corruptos existentes
    print("[INSERT] Eliminando chunks corruptos...")
    old_uids = [
        'ley_lir_art_14_e',
        'ley_lir_art_17',
        'ley_lir_art_17_1',
        'ley_lir_art_17_2',
    ]
    for uid in old_uids:
        try:
            supabase.table('document_chunks').delete().eq('chunk_uid', uid).execute()
            print(f"  Eliminado: {uid}")
        except Exception as e:
            print(f"  {uid}: {e}")

    # Insertar nuevos chunks
    print("[INSERT] Generando embeddings e insertando...")
    for chunk in chunks:
        content = chunk['content']
        if not content or len(content) < 100:
            print(f"  SKIP {chunk['uid']}: contenido muy corto")
            continue

        print(f"  {chunk['uid']} ({len(content)} chars)...")
        embedding = await embed_text(content)

        row = {
            'chunk_uid': chunk['uid'],
            'source_path': 'documents/DL-824_31-DIC-1974.pdf',
            'filename': 'DL-824_31-DIC-1974.pdf',
            'source_type': 'ley',
            'law_tag': 'lir',
            'section_level_name': chunk['section'],
            'content': content,
            'metadata': {'article': chunk['uid'], 'rechunked': True, 'date': '2026-06-05'},
            'content_hash': content_hash(content),
            'is_derogada': False,
            'embedding': embedding,
            'chunk_index': 0,
            'total_chunks': 1,
        }

        try:
            supabase.table('document_chunks').insert(row).execute()
            print(f"    OK insertado")
        except Exception as e:
            print(f"    ERROR: {e}")

    print("[INSERT] Completado")


if __name__ == '__main__':
    asyncio.run(main())
