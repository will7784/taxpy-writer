"""
Auditoria de chunks de leyes en Supabase.

Detecta:
- Chunks cortados (empiezan/terminan a mitad de palabra)
- Chunks corruptos (caracteres de reemplazo �)
- Chunks muy cortos (< 500 chars) o muy largos (> 6000 chars)
- Gaps en secuencia de articulos
- Articulos criticos faltantes
"""

import asyncio
import re
import sys
from pathlib import Path
from dataclasses import dataclass
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase_client import supabase


@dataclass
class ChunkAudit:
    uid: str
    law_tag: str
    article: str | None
    length: int
    has_corrupt_chars: bool
    corrupt_count: int
    starts_midword: bool
    ends_midword: bool
    starts_mid_sentence: bool
    ends_mid_sentence: bool
    first_100: str
    last_100: str


# Articulos criticos que DEBEN existir
CRITICAL_ARTICLES = {
    "lir": [
        "14", "14_a", "14_d", "14_e",   # Regimenes tributarios
        "17",                             # Ingresos no constitutivos de renta
        "20", "21",                       # Rentas de primera categoria
        "31",                             # Gastos deducibles
        "41",                             # Reajustes
        "54",                             # Retiros / dividendos
        "59",                             # Pago provisional
        "62",                             # Credito por donaciones
        "96",                             # IUSC / IGC / Adicional
    ],
    "codigo_tributario": [
        "1", "2", "3",                    # Disposiciones generales
        "8",                              # Relacionados
        "59", "60", "61",                 # Obligaciones formales
        "64",                             # Facultades SII
        "97",                             # Infracciones
        "104",                            # Delitos tributarios
        "120", "121", "122",              # Prescripcion
        "165",                            # Reclamaciones
        "192",                            # PRO-PYME
    ],
    "iva": [
        "1", "2", "3",                    # Disposiciones generales
        "12",                             # Hecho generador
        "14",                             # Exenciones
        "15",                             # Tasa
        "23",                             # Credito fiscal
    ],
}


def extract_article_from_uid(uid: str) -> str | None:
    """Extrae numero de articulo del UID."""
    # ley_lir_art_17_n8 -> 17_n8
    # ley_lir_art_14_e_0 -> 14_e_0
    match = re.search(r'_art_([\w_]+)$', uid)
    if match:
        return match.group(1)
    return None


def analyze_chunk(row: dict) -> ChunkAudit:
    content = row.get("content", "") or ""
    uid = row["chunk_uid"]
    law_tag = row.get("law_tag", "") or ""
    
    # Caracteres corruptos
    corrupt_count = content.count("�")
    has_corrupt = corrupt_count > 0
    
    # Empieza a mitad de palabra?
    first_line = content.split("\n")[0].strip() if content else ""
    starts_midword = bool(first_line) and not first_line[0].isupper() and not first_line[0].isdigit() and first_line[0] not in " -\t\"'¿¡"
    
    # Termina a mitad de palabra?
    last_line = content.split("\n")[-1].strip() if content else ""
    ends_midword = bool(last_line) and last_line[-1] not in ".;:\n!?" and len(last_line) < 50
    
    # Empieza a mitad de oracion?
    first_50 = content[:50] if content else ""
    starts_mid_sentence = bool(re.search(r'^[a-záéíóúñ]', first_50.strip()))
    
    # Termina a mitad de oracion?
    last_50 = content[-50:] if content else ""
    ends_mid_sentence = not bool(re.search(r'[.;:?!]\s*$', last_50.strip()))
    
    return ChunkAudit(
        uid=uid,
        law_tag=law_tag,
        article=extract_article_from_uid(uid),
        length=len(content),
        has_corrupt_chars=has_corrupt,
        corrupt_count=corrupt_count,
        starts_midword=starts_midword,
        ends_midword=ends_midword,
        starts_mid_sentence=starts_mid_sentence,
        ends_mid_sentence=ends_mid_sentence,
        first_100=content[:100].replace("\n", " "),
        last_100=content[-100:].replace("\n", " "),
    )


def numeric_key(article: str) -> tuple:
    """Convierte '14_e_0' -> (14, 'e', 0) para ordenar."""
    parts = article.split("_")
    num = 0
    letter = ""
    sub = 0
    try:
        num = int(parts[0])
    except ValueError:
        pass
    if len(parts) > 1:
        if parts[1].isalpha():
            letter = parts[1]
            if len(parts) > 2:
                try:
                    sub = int(parts[2])
                except ValueError:
                    pass
        else:
            try:
                sub = int(parts[1])
            except ValueError:
                pass
    return (num, letter, sub)


async def main():
    print("[AUDITORIA] Cargando chunks de leyes desde Supabase...")
    resp = supabase.table("document_chunks").select(
        "chunk_uid, law_tag, filename, content, metadata"
    ).eq("source_type", "ley").execute()
    
    rows = resp.data
    print(f"[AUDITORIA] Total chunks de ley: {len(rows)}")
    
    audits = [analyze_chunk(r) for r in rows]
    
    # ── Reporte 1: Chunks con caracteres corruptos ──
    corrupt = [a for a in audits if a.has_corrupt_chars]
    print(f"\n{'='*60}")
    print(f"1. CHUNKS CON CARACTERES CORRUPTOS (U+FFFD): {len(corrupt)}")
    print(f"{'='*60}")
    for a in sorted(corrupt, key=lambda x: -x.corrupt_count)[:20]:
        print(f"  {a.uid} | {a.corrupt_count} corruptos | len={a.length}")
    if len(corrupt) > 20:
        print(f"  ... y {len(corrupt) - 20} mas")
    
    # ── Reporte 2: Chunks muy cortos ──
    too_short = [a for a in audits if a.length < 500]
    print(f"\n{'='*60}")
    print(f"2. CHUNKS MUY CORTOS (< 500 chars): {len(too_short)}")
    print(f"{'='*60}")
    for a in sorted(too_short, key=lambda x: x.length)[:20]:
        print(f"  {a.uid} | len={a.length}")
        print(f"    Inicio: {a.first_100[:60]}...")
    if len(too_short) > 20:
        print(f"  ... y {len(too_short) - 20} mas")
    
    # ── Reporte 3: Chunks muy largos ──
    too_long = [a for a in audits if a.length > 6000]
    print(f"\n{'='*60}")
    print(f"3. CHUNKS MUY LARGOS (> 6000 chars): {len(too_long)}")
    print(f"{'='*60}")
    for a in sorted(too_long, key=lambda x: -x.length)[:15]:
        print(f"  {a.uid} | len={a.length}")
    
    # ── Reporte 4: Chunks cortados (empiezan o terminan mal) ──
    cut = [a for a in audits if a.starts_mid_sentence or a.ends_mid_sentence]
    print(f"\n{'='*60}")
    print(f"4. CHUNKS CORTADOS (empiezan/terminan a mitad de oracion): {len(cut)}")
    print(f"{'='*60}")
    for a in cut[:30]:
        flags = []
        if a.starts_mid_sentence:
            flags.append("INICIO_CORTADO")
        if a.ends_mid_sentence:
            flags.append("FIN_CORTADO")
        print(f"  {a.uid} | {' + '.join(flags)} | len={a.length}")
        if a.starts_mid_sentence:
            print(f"    -> Inicio: {a.first_100[:80]}...")
        if a.ends_mid_sentence:
            print(f"    -> Final:  ...{a.last_100[-80:]}")
    if len(cut) > 30:
        print(f"  ... y {len(cut) - 30} mas")
    
    # ── Reporte 5: Gaps en articulos por ley ──
    print(f"\n{'='*60}")
    print(f"5. GAPS EN SECUENCIA DE ARTICULOS")
    print(f"{'='*60}")
    
    by_law = defaultdict(list)
    for a in audits:
        if a.article:
            by_law[a.law_tag].append(a)
    
    for law in sorted(by_law.keys()):
        articles = sorted([a.article for a in by_law[law]], key=numeric_key)
        # Extraer solo numeros principales para detectar gaps grandes
        main_nums = []
        for art in articles:
            m = re.match(r'(\d+)', art)
            if m:
                main_nums.append(int(m.group(1)))
        
        unique_nums = sorted(set(main_nums))
        gaps = []
        for i in range(len(unique_nums) - 1):
            if unique_nums[i+1] - unique_nums[i] > 5:
                gaps.append((unique_nums[i], unique_nums[i+1]))
        
        if gaps:
            print(f"\n  Ley: {law} ({len(articles)} chunks)")
            for g in gaps[:5]:
                print(f"    Gap: Art. {g[0]} -> Art. {g[1]} (faltan {g[1]-g[0]-1} articulos)")
    
    # ── Reporte 6: Articulos criticos faltantes ──
    print(f"\n{'='*60}")
    print(f"6. ARTICULOS CRITICOS FALTANTES")
    print(f"{'='*60}")
    
    for law, critical_list in CRITICAL_ARTICLES.items():
        existing = set()
        for a in by_law.get(law, []):
            if a.article:
                # Comparar con patrones criticos
                for crit in critical_list:
                    if a.article == crit or a.article.startswith(crit + "_"):
                        existing.add(crit)
        
        missing = set(critical_list) - existing
        if missing:
            print(f"\n  Ley {law}: FALTAN {len(missing)} articulos criticos")
            for m in sorted(missing, key=lambda x: numeric_key(x)):
                print(f"    - Art. {m}")
        else:
            print(f"  Ley {law}: Todos los criticos presentes [OK]")
    
    # ── Reporte 7: Chunks con mismo articulo pero multiples UIDs ──
    print(f"\n{'='*60}")
    print(f"7. ARTICULOS CON MULTIPLES CHUNKS (posible fragmentacion)")
    print(f"{'='*60}")
    
    for law in sorted(by_law.keys()):
        article_uids = defaultdict(list)
        for a in by_law[law]:
            if a.article:
                # Agrupar por numero principal de articulo
                m = re.match(r'(\d+[a-z]?)', a.article)
                if m:
                    article_uids[m.group(1)].append(a.uid)
        
        multi = {k: v for k, v in article_uids.items() if len(v) > 2}
        if multi:
            print(f"\n  Ley {law}:")
            for art, uids in sorted(multi.items(), key=lambda x: numeric_key(x[0]))[:10]:
                print(f"    Art. {art}: {len(uids)} chunks -> {uids}")
    
    print(f"\n{'='*60}")
    print(f"AUDITORIA COMPLETADA")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
