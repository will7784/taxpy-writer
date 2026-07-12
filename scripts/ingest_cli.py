"""
CLI único de ingesta — reemplaza a initial_ingest.py, insert_rechunked.py
y los scripts rechunk_*.py dispersos.

Toda la lógica real vive en ingest.py (IngestionPipeline, PDFLawParser,
JurisprudenciaMDParser, CircularMDParser) y legal_parser.py (segmentación
jerárquica). Este script es solo el punto de entrada.

Uso:
    python scripts/ingest_cli.py --all                    # todo el corpus conocido
    python scripts/ingest_cli.py --leyes                  # solo PDFs de leyes ya registrados
    python scripts/ingest_cli.py --jurisprudencia         # solo jurisprudencia judicial (.md)
    python scripts/ingest_cli.py --circulares             # solo circulares SII (.md)

    # Ley nueva puntual (no requiere código nuevo, solo el patrón ya
    # cubre "ARTICULO N.-" / "Artículo N.-"; ver legal_parser.DOCUMENT_PATTERNS
    # si el formato de la ley es distinto):
    python scripts/ingest_cli.py --pdf documents/nueva_ley.pdf --law-tag mi_ley
    python scripts/ingest_cli.py --pdf documents/nueva_ley.pdf --law-tag mi_ley --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

from ingest import LAW_TAG_FROM_FILENAME, IngestionPipeline, PDFLawParser

console = Console()


async def ingest_single_pdf(path: Path, law_tag: str | None, dry_run: bool) -> int:
    if law_tag:
        LAW_TAG_FROM_FILENAME[path.name.lower()] = law_tag

    chunks = PDFLawParser.parse(path)
    console.print(f"[blue]{path.name}: {len(chunks)} chunks generados[/blue]")

    if dry_run:
        for c in chunks[:10]:
            console.print(f"  [dim]{c.chunk_uid} | {c.hierarchy_path}[/dim]")
        if len(chunks) > 10:
            console.print(f"  [dim]... y {len(chunks) - 10} más[/dim]")
        console.print("[yellow]--dry-run: nada se escribió en Supabase.[/yellow]")
        return len(chunks)

    pipeline = IngestionPipeline()
    await pipeline.upsert_chunks(chunks)
    return len(chunks)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Ingesta unificada de documentos legales")
    ap.add_argument("--all", action="store_true", help="Ingesta todo el corpus conocido")
    ap.add_argument("--leyes", action="store_true", help="Solo PDFs de leyes en documents/*.pdf")
    ap.add_argument("--jurisprudencia", action="store_true", help="Solo jurisprudencia judicial (.md)")
    ap.add_argument("--circulares", action="store_true", help="Solo circulares SII (.md)")
    ap.add_argument("--pdf", type=str, help="Ruta a un PDF de ley puntual a ingestar")
    ap.add_argument("--law-tag", type=str, help="law_tag a usar con --pdf (ej: 'lir', 'codigo_tributario')")
    ap.add_argument("--dry-run", action="store_true", help="Solo mostrar qué se generaría, sin escribir en Supabase")
    args = ap.parse_args()

    if args.pdf:
        await ingest_single_pdf(Path(args.pdf), args.law_tag, args.dry_run)
        return

    if args.dry_run:
        console.print("[yellow]--dry-run solo está soportado junto con --pdf por ahora.[/yellow]")
        return

    pipeline = IngestionPipeline()
    selected = args.leyes or args.jurisprudencia or args.circulares

    if args.all or not selected:
        results = await pipeline.ingest_all()
    else:
        results = {}
        if args.leyes:
            results["leyes_pdf"] = await pipeline.ingest_leyes_pdf()
        if args.jurisprudencia:
            results["jurisprudencia_judicial"] = await pipeline.ingest_jurisprudencia_judicial()
        if args.circulares:
            results["circulares"] = await pipeline.ingest_circulares()

    console.print("[bold green]Resumen de ingesta:[/bold green]")
    for key, count in results.items():
        console.print(f"  • {key}: {count} chunks")


if __name__ == "__main__":
    asyncio.run(main())
