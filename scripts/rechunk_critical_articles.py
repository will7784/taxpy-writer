"""
Re-chunkifica artículos críticos de la LIR (Art. 14 E y Art. 17 N° 8)
para corregir chunks corruptos y demasiado grandes.

Uso:
    python scripts/rechunk_critical_articles.py
"""

import asyncio
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import fitz
from openai import AsyncOpenAI

import config
from models import DocumentChunk
from supabase_client import supabase


def clean_text(text: str) -> str:
    """Limpia texto extraído del PDF."""
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        # Eliminar líneas de pie de página
        if any(x in line for x in [
            'Decreto Ley 824',
            'Biblioteca del Congreso',
            'www.leychile.cl',
            'documento generado',
            'página',
            'pagina',
        ]):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)


def extract_article_14_e(full_text: str) -> str:
    """Extrae la letra E del Art. 14."""
    idx14 = full_text.find('ARTICULO 14')
    idx15 = full_text.find('ARTICULO 15')
    art14 = full_text[idx14:idx15]

    idxE = art14.find('E)')
    idx_end = art14.find('F)', idxE + 10)
    if idx_end < 0:
        idx_end = art14.find('ARTICULO 14 BIS', idxE + 10)
    if idx_end < 0:
        idx_end = len(art14)

    return clean_text(art14[idxE:idx_end].strip())


def extract_article_17_n8(full_text: str) -> str:
    """Extrae el N° 8 del Art. 17 (ganancias de capital / venta de inmuebles)."""
    idx17 = full_text.find('ARTICULO 17')
    idx18 = full_text.find('ARTICULO 18')
    art17 = full_text[idx17:idx18]

    # Buscar 8.000 UF como ancla
    idx_8000 = art17.find('8.000 unidades de fomento')
    if idx_8000 < 0:
        idx_8000 = art17.find('8.000')

    # Buscar hacia atrás hasta encontrar el inicio del N° 8
    # El N° 8 trata sobre "mayor valor obtenido en la enajenación"
    start = 0
    for i in range(idx_8000, 0, -1):
        if art17[i:i+20].startswith('    8.') or art17[i:i+20].startswith('   8.'):
            start = i
            break

    # Si no encontramos, buscar "mayor valor obtenido"
    if start == 0:
        idx_mayor = art17.find('mayor valor obtenido')
        if idx_mayor > 0:
            # Buscar linea que empiece con numero antes de esto
            lines_before = art17[:idx_mayor].split('\n')
            for j in range(len(lines_before) - 1, max(0, len(lines_before) - 20), -1):
                if lines_before[j].strip().startswith('8.'):
                    start = len('\n'.join(lines_before[:j]))
                    break

    # Buscar hacia adelante hasta el N° 9
    end = len(art17)
    idx9 = art17.find('9.', start + 50)
    if idx9 > 0:
        end = idx9

    return clean_text(art17[start:end].strip())


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
    print("[RECHUNK] Extrayendo texto del PDF...")
    doc = fitz.open('documents/DL-824_31-DIC-1974.pdf')
    full_text = ''
    for page in doc:
        full_text += page.get_text() + '\n'

    # Extraer secciones
    art14_e_text = extract_article_14_e(full_text)
    art17_n8_text = extract_article_17_n8(full_text)

    print(f"[RECHUNK] Art. 14 E: {len(art14_e_text)} chars")
    print(f"[RECHUNK] Art. 17 N° 8: {len(art17_n8_text)} chars")

    if len(art17_n8_text) < 500:
        print("[WARN] Art. 17 N° 8 parece muy corto, abortando")
        return

    # Dividir Art. 14 E en sub-chunks si es muy largo
    chunks_to_insert = []

    # Chunk 1: Art. 14 E completo (o dividido en 2 si es necesario)
    if len(art14_e_text) > 4000:
        mid = len(art14_e_text) // 2
        # Buscar salto de linea cerca de la mitad
        split_at = art14_e_text.find('\n\n', mid - 200)
        if split_at < 0:
            split_at = mid
        chunks_to_insert.append({
            'uid': 'ley_lir_art_14_e_0',
            'content': art14_e_text[:split_at].strip(),
        })
        chunks_to_insert.append({
            'uid': 'ley_lir_art_14_e_1',
            'content': art14_e_text[split_at:].strip(),
        })
    else:
        chunks_to_insert.append({
            'uid': 'ley_lir_art_14_e_0',
            'content': art14_e_text,
        })

    # Chunk: Art. 17 N° 8
    chunks_to_insert.append({
        'uid': 'ley_lir_art_17_n8',
        'content': art17_n8_text,
    })

    # Eliminar chunks corruptos existentes
    print("[RECHUNK] Eliminando chunks corruptos existentes...")
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
            print(f"  No se pudo eliminar {uid}: {e}")

    # Insertar chunks nuevos
    print("[RECHUNK] Generando embeddings e insertando chunks nuevos...")
    for chunk_data in chunks_to_insert:
        content = chunk_data['content']
        if not content:
            continue

        print(f"  Procesando {chunk_data['uid']} ({len(content)} chars)...")
        embedding = await embed_text(content)

        row = {
            'chunk_uid': chunk_data['uid'],
            'source_path': 'documents/DL-824_31-DIC-1974.pdf',
            'filename': 'DL-824_31-DIC-1974.pdf',
            'source_type': 'ley',
            'law_tag': 'lir',
            'section_level_name': chunk_data['uid'].replace('ley_lir_', '').replace('_', ' ').upper(),
            'content': content,
            'metadata': {'article': chunk_data['uid'], 'rechunked': True},
            'content_hash': content_hash(content),
            'is_derogada': False,
            'embedding': embedding,
            'chunk_index': 0,
            'total_chunks': 1,
        }

        try:
            supabase.table('document_chunks').insert(row).execute()
            print(f"  Insertado: {chunk_data['uid']}")
        except Exception as e:
            print(f"  Error insertando {chunk_data['uid']}: {e}")

    print("[RECHUNK] Completado")


if __name__ == '__main__':
    asyncio.run(main())
