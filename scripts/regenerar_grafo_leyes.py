"""
Regenera el grafo de leyes con relaciones basadas en reglas.

Relaciones generadas:
- precede_a: Articulo N -> Articulo N+1 (misma ley)
- menciona: Articulo que cita otro articulo en su texto
- pertenece_a: Articulo -> Ley
"""

import re
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase_client import supabase


def extract_article_number(uid: str) -> int | None:
    """Extrae numero de articulo de UID tipo ley_lir_art_14_e_1."""
    m = re.search(r'_art_(\d+)', uid)
    if m:
        return int(m.group(1))
    return None


def extract_cited_articles(text: str, law_tag: str) -> list[tuple[str, str]]:
    """
    Extrae articulos mencionados en el texto.
    Retorna lista de (law_tag_citado, numero_articulo).
    """
    cited = []

    # Patrones de citacion
    patterns = [
        # "artículo 21", "art. 21"
        r'(?:art[íi]culo|art\.?)\s+(\d+)[°\u00ba]?',
        # "el artículo 59 del Código Tributario"
        r'(?:art[íi]culo|art\.?)\s+(\d+)[°\u00ba]?.*?c[oó]digo tributario',
        r'(?:art[íi]culo|art\.?)\s+(\d+)[°\u00ba]?.*?ley de renta',
        r'(?:art[íi]culo|art\.?)\s+(\d+)[°\u00ba]?.*?ley del iva',
    ]

    found_nums = set()
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            num = m.group(1)
            if num not in found_nums:
                found_nums.add(num)
                # Determinar ley citada
                cit_law = law_tag  # default: misma ley
                ctx = text[m.end():m.end()+100].lower()
                if 'código tributario' in ctx or 'codigo tributario' in ctx:
                    cit_law = 'codigo_tributario'
                elif 'ley de renta' in ctx or 'impuesto a la renta' in ctx:
                    cit_law = 'lir'
                elif 'iva' in ctx:
                    cit_law = 'iva'
                cited.append((cit_law, num))

    return cited


def main():
    print("[GRAFO] Cargando chunks de leyes...")
    resp = supabase.table("document_chunks").select(
        "chunk_uid, law_tag, content, chunk_index, total_chunks"
    ).eq("source_type", "ley").execute()

    chunks = resp.data
    print(f"[GRAFO] {len(chunks)} chunks cargados")

    # Indexar por ley y numero de articulo
    by_law: dict[str, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for c in chunks:
        law = c["law_tag"]
        art_num = extract_article_number(c["chunk_uid"])
        if art_num is not None:
            by_law[law][art_num].append(c)

    relations = []
    relation_key = set()  # evitar duplicados

    def add_rel(src: str, tgt: str, rel_type: str):
        key = (src, tgt, rel_type)
        if key in relation_key:
            return
        relation_key.add(key)
        relations.append({
            "source_chunk_uid": src,
            "target_chunk_uid": tgt,
            "relation_type": rel_type,
            "confidence": 1.0,
        })

    # ── Relacion 1: precede_a (articulo N -> N+1) ──
    print("[GRAFO] Generando relaciones precede_a...")
    for law, arts in by_law.items():
        nums = sorted(arts.keys())
        for i in range(len(nums) - 1):
            curr_chunks = arts[nums[i]]
            next_chunks = arts[nums[i + 1]]
            # Conectar el ultimo chunk del articulo actual con el primero del siguiente
            if curr_chunks and next_chunks:
                src = sorted(curr_chunks, key=lambda x: x["chunk_index"])[-1]["chunk_uid"]
                tgt = sorted(next_chunks, key=lambda x: x["chunk_index"])[0]["chunk_uid"]
                add_rel(src, tgt, "precede_a")

    # ── Relacion 2: menciona (articulo que cita otro) ──
    print("[GRAFO] Generando relaciones menciona...")
    # Indexar todos los chunks por (law, art_num) para buscar targets
    chunk_index = {}
    for c in chunks:
        law = c["law_tag"]
        art_num = extract_article_number(c["chunk_uid"])
        if art_num is not None:
            chunk_index[(law, art_num)] = c["chunk_uid"]

    for c in chunks:
        src_uid = c["chunk_uid"]
        law = c["law_tag"]
        text = c.get("content", "") or ""
        cited = extract_cited_articles(text, law)
        for cit_law, cit_num in cited:
            try:
                cit_num_int = int(cit_num)
                tgt_uid = chunk_index.get((cit_law, cit_num_int))
                if tgt_uid and tgt_uid != src_uid:
                    add_rel(src_uid, tgt_uid, "menciona")
            except ValueError:
                pass

    print(f"[GRAFO] Total relaciones a insertar: {len(relations)}")

    # ── Insertar en Supabase ──
    BATCH_SIZE = 500
    inserted = 0
    for i in range(0, len(relations), BATCH_SIZE):
        batch = relations[i:i + BATCH_SIZE]
        try:
            supabase.table("knowledge_graph").insert(batch).execute()
            inserted += len(batch)
            print(f"  Batch {i//BATCH_SIZE + 1}: +{len(batch)}")
        except Exception as e:
            print(f"  [ERROR] Batch {i//BATCH_SIZE + 1}: {e}")

    print(f"[GRAFO] Insertadas: {inserted} relaciones")


if __name__ == "__main__":
    main()
