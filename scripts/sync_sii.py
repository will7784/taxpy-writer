"""
Sincronización diaria de jurisprudencia del SII.

Uso:
    python scripts/sync_sii.py --full          # Sincronización completa
    python scripts/sync_sii.py --incremental   # Solo novedades (default)
    python scripts/sync_sii.py --circulares    # Solo circulares
    python scripts/sync_sii.py --acj           # Solo jurisprudencia judicial

Este script solo escribe los .md nuevos en documents/jurisprudencia_sii/ y
documents/jurisprudencia_sii_circulares/. Para subirlos a Supabase (embeddings
+ dedupe por content_hash), correr después:
    python sync_sii.py (descarga jurisprudencia y circulares del SII)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

from scrapers.sii_acj import SIIACJScraper
from scrapers.sii_circulares import SIICircularesScraper

console = Console()

SYNC_STATE_FILE = Path("documents/jurisprudencia_sii/_sync_state.json")


def load_sync_state() -> dict:
    if SYNC_STATE_FILE.exists():
        return json.loads(SYNC_STATE_FILE.read_text(encoding="utf-8"))
    return {
        "last_sync": None,
        "cuerpo_normativo": 2,
        "articulos": {},
        "pronunciamientos": {},
    }


def save_sync_state(state: dict) -> None:
    SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


async def sync_circulares() -> dict:
    """Sincroniza circulares del SII."""
    console.print("[bold blue]🔄 Sincronizando circulares SII...[/bold blue]")
    scraper = SIICircularesScraper()
    try:
        results = await scraper.sync_circulares()
    finally:
        await scraper.close()
    return results


async def sync_acj(full: bool = False) -> dict:
    """Sincroniza jurisprudencia judicial del SII ACJ."""
    console.print("[bold blue]🔄 Sincronizando jurisprudencia judicial SII ACJ...[/bold blue]")
    scraper = SIIACJScraper()
    state = load_sync_state()

    try:
        # Listar cuerpos normativos
        cuerpos = await scraper.list_cuerpos_normativos()
        console.print(f"  Cuerpos normativos: {len(cuerpos)}")

        total_pron = 0
        total_new = 0

        target_cuerpos = [c for c in cuerpos if c.get("id") in {1, 2, 3}]
        if not target_cuerpos:
            console.print("[yellow]⚠️ No se encontraron los cuerpos Código Tributario, LIR o IVA[/yellow]")
            return {"total": 0, "new": 0}

        # Aun en modo incremental se vuelve a consultar cada artículo: un fallo
        # nuevo puede asociarse a un artículo ya visto en una ejecución anterior.
        for cuerpo in target_cuerpos:
            cuerpo_id = cuerpo.get("id")
            cuerpo_nombre = str(cuerpo.get("nombre", cuerpo_id))
            articulos = await scraper.find_articulos(cuerpo_id)
            console.print(f"  {cuerpo_nombre}: {len(articulos)} artículos")
            for articulo in articulos:
                art_id = articulo.get("id")
                art_nombre = str(articulo.get("nombre", art_id))
                if not art_id:
                    continue

                pronunciamientos = await scraper.find_pronunciamientos(art_id, cuerpo_normativo_id=cuerpo_id)

                for pron in pronunciamientos:
                    pron_id = pron.get("id")
                    if not pron_id:
                        continue

                    # Identidad compuesta evita colisiones entre cuerpos.
                    pron_key = f"{cuerpo_id}_{art_id}_{pron_id}"
                    if not full and pron_key in state["pronunciamientos"]:
                        continue

                    full_pron = await scraper.get_full_pronunciamiento(pron_id)
                    if not full_pron:
                        continue

                    md_content = scraper.pron_to_md(full_pron, art_nombre, cuerpo_normativo_id=cuerpo_id,
                                                      cuerpo_nombre=cuerpo_nombre)
                    art_dir = scraper.output_dir / f"cuerpo_{cuerpo_id}" / f"art_{art_nombre.replace(' ', '_')}"
                    art_dir.mkdir(parents=True, exist_ok=True)
                    md_path = art_dir / f"sii_pron_{pron_id}.md"
                    md_path.write_text(md_content, encoding="utf-8")

                    total_pron += 1
                    if pron_key not in state["pronunciamientos"]:
                        total_new += 1

                    state["pronunciamientos"][pron_key] = {
                        "pron_id": pron_id, "articulo_id": art_id, "cuerpo_normativo_id": cuerpo_id,
                        "fecha_sync": datetime.utcnow().isoformat(),
                    }

                state["articulos"][f"{cuerpo_id}_{art_id}"] = {
                    "nombre": art_nombre, "cuerpo_normativo_id": cuerpo_id,
                    "fecha_sync": datetime.utcnow().isoformat(),
                }

        state["last_sync"] = datetime.utcnow().isoformat()
        save_sync_state(state)

    except Exception as e:
        console.print(f"[red]❌ Error en sync ACJ: {e}[/red]")
        import traceback
        console.print(traceback.format_exc())
    finally:
        await scraper.close()

    console.print(f"[green]✅ ACJ: {total_pron} pronunciamientos procesados ({total_new} nuevos)[/green]")
    return {"total": total_pron, "new": total_new}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sincronización de jurisprudencia SII")
    parser.add_argument("--full", action="store_true", help="Sincronización completa (no incremental)")
    parser.add_argument("--circulares", action="store_true", help="Solo circulares")
    parser.add_argument("--acj", action="store_true", help="Solo jurisprudencia judicial")
    args = parser.parse_args()

    results = {}

    if args.circulares or not args.acj:
        results["circulares"] = await sync_circulares()

    if args.acj or not args.circulares:
        results["acj"] = await sync_acj(full=args.full)

    console.print("\n[bold green]📊 Resumen de sincronización:[/bold green]")
    for key, val in results.items():
        console.print(f"  {key}: {val}")


if __name__ == "__main__":
    asyncio.run(main())
