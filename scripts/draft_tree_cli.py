"""
Genera un borrador de árbol de decisión a partir de un chunk ya ingestado.

El borrador queda en decision_trees/_drafts/ para revisión humana en
/review/drafts (web_server.py) — nunca se publica directo.

Uso:
    python scripts/draft_tree_cli.py --chunk-uid ley_codigo_tributario_art_63
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

from decision_tree_drafter import draft_tree_from_chunk, save_draft
from graph_engine import graph as graph_engine
from llm_client import LLMClient

console = Console()


async def main() -> None:
    ap = argparse.ArgumentParser(description="Genera un borrador de árbol de decisión")
    ap.add_argument("--chunk-uid", required=True, help="chunk_uid del artículo (ej: ley_codigo_tributario_art_63)")
    args = ap.parse_args()

    chunk = await graph_engine.get_chunk_by_uid(args.chunk_uid)
    if not chunk:
        console.print(f"[red]No se encontró el chunk {args.chunk_uid!r} en document_chunks.[/red]")
        return

    console.print(f"[blue]Generando borrador para {chunk.chunk_uid} ({chunk.section_level_name})...[/blue]")
    llm = LLMClient()
    tree = await draft_tree_from_chunk(chunk, llm)
    path = save_draft(tree)
    console.print(f"[green]Borrador guardado en {path}[/green]")
    console.print("[dim]Revísalo en /review/drafts antes de aprobarlo.[/dim]")


if __name__ == "__main__":
    asyncio.run(main())
