"""
Script de ingestión inicial.

Uso:
    python scripts/initial_ingest.py

Ingesta todo el corpus existente en documents/ a Supabase pgvector.
Este script se ejecuta UNA VEZ al inicializar el proyecto.
"""

import asyncio
import sys
from pathlib import Path

# Asegurar que el proyecto raíz esté en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

from ingest import IngestionPipeline

console = Console()


async def main() -> None:
    console.print("[bold blue]🚀 Ingestión inicial de corpus ImpuestIA[/bold blue]")
    console.print("Este proceso puede tardar varios minutos...\n")

    pipeline = IngestionPipeline()
    results = await pipeline.ingest_all()

    console.print("\n[bold green]✅ Ingestión completada[/bold green]")
    for key, count in results.items():
        console.print(f"  • {key}: {count} chunks")

    total = sum(results.values())
    console.print(f"\n[bold]Total: {total} chunks ingestados[/bold]")


if __name__ == "__main__":
    asyncio.run(main())
