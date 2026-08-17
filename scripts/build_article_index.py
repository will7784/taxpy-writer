"""
Construye (o actualiza) el indice maestro articulo <-> documentos.

Escanea el vault de Obsidian y los directorios de jurisprudencia scrapeada,
extrae las citas a articulos de cada .md y persiste knowledge/article_index.json.

Uso:
    python scripts/build_article_index.py            # rebuild incremental
    python scripts/build_article_index.py --force    # rebuild completo
    python scripts/build_article_index.py --sync     # primero corre sync_sii (scrapers SII)
    python scripts/build_article_index.py --stats    # solo muestra estadisticas
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

from article_index import article_index

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild del indice maestro de articulos")
    parser.add_argument("--force", action="store_true", help="Rebuild completo (ignora mtimes)")
    parser.add_argument("--sync", action="store_true", help="Corre scripts/sync_sii.py antes (scrapers SII)")
    parser.add_argument("--stats", action="store_true", help="Solo muestra estadisticas del indice actual")
    args = parser.parse_args()

    if args.stats:
        stats = article_index.stats()
        console.print(f"[cyan]Indice actual:[/cyan] {stats['docs']} documentos, {stats['refs']} referencias (ley, articulo)")
        return

    if args.sync:
        console.print("[bold blue]Sincronizando jurisprudencia SII (scrapers)...[/bold blue]")
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "sync_sii.py"), "--incremental"],
            cwd=Path(__file__).parent.parent,
        )
        if result.returncode != 0:
            console.print("[yellow]sync_sii termino con errores; se reconstruye el indice igual.[/yellow]")

    console.print("[bold blue]Reconstruyendo indice de articulos...[/bold blue]")
    stats = article_index.rebuild(force=args.force)
    final = article_index.stats()
    console.print(
        f"[green]Listo.[/green] Escaneados: {stats['scanned']} | "
        f"Actualizados: {stats['updated']} | Eliminados: {stats['removed']}\n"
        f"Total en indice: {final['docs']} documentos, {final['refs']} referencias."
    )


if __name__ == "__main__":
    main()
