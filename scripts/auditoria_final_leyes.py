"""
Auditoria exhaustiva de calidad de chunks de leyes y grafo.

Verifica:
1. Todos los articulos de cada PDF estan presentes en chunks
2. Chunks empiezan y terminan con oraciones completas
3. No hay chunks basura (< 200 chars)
4. No hay chunks sin dividir (> 4000 chars)
5. Grafo: sin relaciones rotas, cobertura de articulos
6. Articulos criticos: contenido completo y correcto
"""

import re
import fitz
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase_client import supabase


def find_all_articles_pdf(pdf_path: str) -> list[int]:
    """Encuentra todos los numeros de articulo en un PDF."""
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text() + "\n"
    doc.close()

    patterns = [
        r'ARTICULO\s+(\d+)[°\u00ba]?\.?\s*-',
        r'Art\u00edculo\s+(\d+)[°\u00ba]?\.?\s*-',
    ]
    articles = set()
    for pat in patterns:
        for m in re.finditer(pat, text):
            articles.add(int(m.group(1)))
    return sorted(articles)


def sentence_ends_well(text: str) -> bool:
    """Verifica si el texto termina con fin de oracion."""
    if not text:
        return False
    stripped = text.strip()
    return any(stripped.endswith(c) for c in '.;:!?')


def sentence_starts_well(text: str) -> bool:
    """Verifica si el texto empieza con mayuscula o articulo."""
    if not text:
        return False
    stripped = text.strip()
    # Articulo, numero, mayuscula, o inicio de letra
    return bool(re.match(r'^(ART|Art|\d|[A-ZÁÉÍÓÚÑ])', stripped))


def main():
    print("=" * 70)
    print("AUDITORIA FINAL DE LEYES Y GRAFO")
    print("=" * 70)

    PDFS = {
        "documents/DL-824_31-DIC-1974.pdf": "lir",
        "documents/DL-830_31-DIC-1974_codigo tributario.pdf": "codigo_tributario",
        "documents/DL-825_31-DIC-1974.pdf": "iva",
    }

    # Cargar chunks de Supabase
    print("\n[1] Cargando chunks de leyes desde Supabase...")
    resp = supabase.table("document_chunks").select(
        "chunk_uid, law_tag, content, chunk_index, total_chunks"
    ).eq("source_type", "ley").execute()
    chunks = resp.data
    print(f"    Total chunks: {len(chunks)}")

    # Indexar por ley
    by_law = defaultdict(list)
    for c in chunks:
        by_law[c["law_tag"]].append(c)

    # =====================================================================
    # SECCION A: Cobertura de articulos
    # =====================================================================
    print("\n" + "=" * 70)
    print("[A] COBERTURA DE ARTICULOS")
    print("=" * 70)

    for pdf_path, law_tag in PDFS.items():
        print(f"\n  Ley: {law_tag}")
        pdf_articles = find_all_articles_pdf(pdf_path)
        print(f"    Articulos en PDF: {len(pdf_articles)} ({pdf_articles[0]}-{pdf_articles[-1]})")

        # Articulos presentes en chunks
        present_articles = set()
        for c in by_law[law_tag]:
            m = re.search(r'_art_(\d+)', c["chunk_uid"])
            if m:
                present_articles.add(int(m.group(1)))

        missing = sorted(set(pdf_articles) - present_articles)
        extra = sorted(present_articles - set(pdf_articles))

        print(f"    Articulos en chunks: {len(present_articles)}")
        if missing:
            print(f"    [WARN] FALTAN {len(missing)} articulos: {missing[:20]}{'...' if len(missing) > 20 else ''}")
        else:
            print(f"    [OK] Todos los articulos del PDF estan presentes")

        if extra:
            print(f"    [INFO] Articulos extras (transitorios/especiales): {sorted(extra)[:10]}")

    # =====================================================================
    # SECCION B: Calidad de chunks
    # =====================================================================
    print("\n" + "=" * 70)
    print("[B] CALIDAD DE CHUNKS")
    print("=" * 70)

    issues = []
    for c in chunks:
        content = c.get("content", "") or ""
        uid = c["chunk_uid"]
        problems = []

        if len(content) < 200:
            problems.append(f"muy_corto({len(content)})")
        if len(content) > 4000:
            problems.append(f"muy_largo({len(content)})")
        if not sentence_starts_well(content):
            problems.append("inicio_malo")
        if not sentence_ends_well(content):
            problems.append("fin_malo")

        if problems:
            issues.append((uid, problems, len(content)))

    print(f"\n  Total chunks con problemas: {len(issues)}/{len(chunks)}")

    # Mostrar problemas graves primero
    severe = [(u, p, l) for u, p, l in issues if "muy_corto" in str(p) or "muy_largo" in str(p)]
    if severe:
        print(f"\n  [SEVERO] Chunks muy cortos o muy largos:")
        for uid, probs, length in severe[:15]:
            print(f"    {uid} | {length} chars | {', '.join(probs)}")
        if len(severe) > 15:
            print(f"    ... y {len(severe) - 15} mas")

    mild = [(u, p, l) for u, p, l in issues if not any(x in str(p) for x in ["muy_corto", "muy_largo"])]
    if mild:
        print(f"\n  [LEVE] Chunks con inicio/fin de oracion dudoso: {len(mild)}")
        for uid, probs, length in mild[:10]:
            print(f"    {uid} | {length} chars | {', '.join(probs)}")
        if len(mild) > 10:
            print(f"    ... y {len(mild) - 10} mas")

    if not issues:
        print("  [OK] Todos los chunks pasan la auditoria de calidad")

    # =====================================================================
    # SECCION C: Articulos criticos
    # =====================================================================
    print("\n" + "=" * 70)
    print("[C] ARTICULOS CRITICOS")
    print("=" * 70)

    critical_checks = [
        ("ley_lir_art_14_e", ["incentivo al ahorro", "100.000", "microempresas"]),
        ("ley_lir_art_14_e_1", ["50%", "reinvertida", "5.000 UF"]),
        ("ley_lir_art_17_n8", ["8.000", "unidades de fomento", "bienes raices"]),
        ("ley_lir_art_17_n8_1", ["un año", "transcurra", "adquisicion"]),
        ("ley_lir_art_20", ["primera categoria", "empresas"]),
        ("ley_lir_art_21", ["retiros", "dividendos", "impuestos finales"]),
        ("ley_lir_art_31", ["gastos deducibles", "renta liquida"]),
        ("ley_lir_art_54", ["retiros", "remesas", "distribuciones"]),
        ("ley_codigo_tributario_art_59", ["fiscalizacion", "plazos"]),
        ("ley_codigo_tributario_art_97", ["infracciones", "sanciones"]),
        ("ley_codigo_tributario_art_192", ["PRO-PYME", "microempresas"]),
        ("ley_iva_art_12", ["exentos", "exportacion"]),
        ("ley_iva_art_15", ["tasa", "19%"]),
    ]

    for uid, keywords in critical_checks:
        found = [c for c in chunks if c["chunk_uid"] == uid]
        if not found:
            print(f"  [FALTA] {uid}: NO EXISTE")
            continue

        content = found[0]["content"].lower()
        missing_kw = [kw for kw in keywords if kw.lower() not in content]

        if missing_kw:
            print(f"  [WARN] {uid}: Faltan keywords {missing_kw}")
        else:
            print(f"  [OK] {uid}: {' | '.join(keywords[:3])}")

    # =====================================================================
    # SECCION D: Grafo
    # =====================================================================
    print("\n" + "=" * 70)
    print("[D] AUDITORIA DE GRAFO")
    print("=" * 70)

    # Contar relaciones
    resp_g = supabase.table("knowledge_graph").select("*", count="exact").limit(1).execute()
    total_rel = resp_g.count
    print(f"\n  Total relaciones: {total_rel}")

    # Verificar relaciones rotas
    existing_uids = set(c["chunk_uid"] for c in chunks)
    resp_rel = supabase.table("knowledge_graph").select("id, source_chunk_uid, target_chunk_uid, relation_type").execute()
    broken = []
    for r in resp_rel.data:
        if r["source_chunk_uid"] not in existing_uids or r["target_chunk_uid"] not in existing_uids:
            broken.append(r)

    if broken:
        print(f"  [WARN] Relaciones rotas: {len(broken)}/{total_rel}")
    else:
        print(f"  [OK] Ninguna relacion rota")

    # Tipos de relacion
    from collections import Counter
    types = Counter(r["relation_type"] for r in resp_rel.data)
    print(f"\n  Tipos de relacion:")
    for t, c in types.most_common():
        print(f"    {t}: {c}")

    # Cobertura: articulos con al menos 1 relacion
    arts_with_rel = set()
    for r in resp_rel.data:
        m = re.search(r'_art_(\d+)', r["source_chunk_uid"])
        if m:
            arts_with_rel.add(r["source_chunk_uid"])

    print(f"\n  Articulos con al menos 1 relacion: {len(arts_with_rel)}")

    # =====================================================================
    # RESUMEN
    # =====================================================================
    print("\n" + "=" * 70)
    print("RESUMEN")
    print("=" * 70)
    print(f"  Chunks de ley: {len(chunks)}")
    print(f"  Chunks con problemas: {len(issues)}")
    print(f"  Relaciones grafo: {total_rel}")
    print(f"  Relaciones rotas: {len(broken)}")
    print(f"  Articulos criticos verificados: {len(critical_checks)}")

    if len(issues) == 0 and len(broken) == 0:
        print("\n  [OK] AUDITORIA APROBADA - Todo listo para produccion")
    else:
        print(f"\n  [ATENCION] Hay {len(issues)} chunks con problemas y {len(broken)} relaciones rotas")


if __name__ == "__main__":
    main()
