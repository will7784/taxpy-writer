"""
Evalúa con evidencia si el grafo de conocimiento (knowledge_graph) mejora
las respuestas sobre RAG vectorial puro, usando consultas REALES de
usage_logs (no un umbral de volumen de datos).

Requiere que telegram_mvp_bot.py ya esté registrando queries (columna
query_text agregada vía sql/002_usage_logs_query_text.sql) — sin eso, la
tabla está vacía y no hay nada que evaluar todavía.

Uso:
    python scripts/eval_graph_lift.py                  # últimas 30 queries reales
    python scripts/eval_graph_lift.py --limit 50
    python scripts/eval_graph_lift.py --demo            # preview con preguntas de ejemplo
                                                          # (NO usar para decidir, solo para
                                                          #  ver el formato del reporte)

Cómo leer el reporte: por cada query se muestra qué chunks trajo el RAG
puro y qué chunks AGREGÓ el grafo (vecinos de esos resultados). Revisa a
mano si esos chunks agregados son relevantes. Si sistemáticamente aportan
señal que el RAG puro no traía → vale la pena invertir en la Fase 5
(resolver la duplicidad regex/LLM del grafo + envolverlo en networkx).
Si no → se deja el grafo como está, sin más inversión por ahora.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

from graph_engine import graph as graph_engine
from rag_engine import rag as rag_engine
from supabase_client import supabase

console = Console()

DEMO_QUERIES = [
    "el sii me citó, ¿qué hago?",
    "me llegó una liquidación de oficio, ¿puedo reclamar?",
    "¿cuánto interés cobran por mora en impuestos?",
    "quiero saber si mi deuda tributaria ya prescribió",
]


def _fetch_recent_queries(limit: int) -> list[str]:
    try:
        resp = (
            supabase.table("usage_logs")
            .select("query_text")
            .not_.is_("query_text", "null")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as e:
        console.print(f"[red]No se pudo consultar usage_logs: {e}[/red]")
        console.print("[yellow]¿Ya corriste sql/002_usage_logs_query_text.sql en Supabase?[/yellow]")
        return []

    return [row["query_text"] for row in resp.data if row.get("query_text")]


async def _evaluate_query(query: str) -> None:
    console.rule(f"[bold]{query}")

    baseline = await rag_engine.search_for_conversation(query)
    baseline_uids = [r.chunk.chunk_uid for r in baseline]

    console.print(f"[blue]RAG puro ({len(baseline)} resultados):[/blue]")
    for r in baseline:
        console.print(f"  • {r.chunk.chunk_uid} (similarity={r.similarity:.3f}) — {r.chunk.section_level_name}")

    if not baseline_uids:
        console.print("[dim]  (sin resultados base, no hay desde dónde expandir el grafo)[/dim]")
        return

    expanded_uids = graph_engine.expand_results(baseline_uids, top_n=5)

    if not expanded_uids:
        console.print("[dim]Grafo: no agregó chunks nuevos para esta query.[/dim]")
        return

    console.print(f"[magenta]Grafo agregó {len(expanded_uids)} chunks adicionales:[/magenta]")
    for uid in expanded_uids:
        chunk = await graph_engine.get_chunk_by_uid(uid)
        label = chunk.section_level_name if chunk else "(no encontrado)"
        console.print(f"  • {uid} — {label}  [dim]<- revisar a mano si es relevante[/dim]")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Evalúa el aporte del grafo sobre queries reales")
    ap.add_argument("--limit", type=int, default=30, help="Cuántas queries reales recientes evaluar")
    ap.add_argument("--demo", action="store_true", help="Usar preguntas de ejemplo en vez de usage_logs (solo para previsualizar el formato, NO para decidir)")
    args = ap.parse_args()

    if args.demo:
        console.print("[yellow]--demo: usando preguntas de ejemplo, NO queries reales. No uses esto para decidir la Fase 5.[/yellow]\n")
        queries = DEMO_QUERIES
    else:
        queries = _fetch_recent_queries(args.limit)
        if not queries:
            console.print(
                "[yellow]Aún no hay queries reales registradas en usage_logs.[/yellow]\n"
                "Deja correr el bot de Telegram unos días con uso real y vuelve a correr este script.\n"
                "(Usa --demo si solo quieres ver el formato del reporte mientras tanto.)"
            )
            return
        console.print(f"[green]Evaluando {len(queries)} queries reales de usage_logs...[/green]\n")

    for query in queries:
        await _evaluate_query(query)

    console.print("\n[bold]Siguiente paso:[/bold] revisa el reporte arriba a mano. Si los chunks que agregó "
                  "el grafo son sistemáticamente relevantes, se invierte en la Fase 5. Si no, se deja el grafo "
                  "como está.")


if __name__ == "__main__":
    asyncio.run(main())
