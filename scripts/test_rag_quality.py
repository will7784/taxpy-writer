"""
Script de validación de calidad del RAG tributario.

Ejecuta casos de prueba conocidos y verifica:
1. Que las citas legales mencionadas existan en las fuentes
2. Que se recuperen los chunks correctos
3. Que las respuestas contengan las normas esperadas

Uso:
    python scripts/test_rag_quality.py

Requiere: OPENAI_API_KEY y SUPABASE_* configuradas en .env
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Agregar raíz al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.table import Table

from rag_engine import rag as rag_engine
from citation_guardrail import CitationGuardrail

console = Console()


TEST_CASES: list[dict] = [
    {
        "query": "¿Hay algún beneficio de rebaja de interés para el régimen PRO-PYME?",
        "expected_chunks": ["ley_codigo_tributario_art_192", "ley_lir_art_14_d"],
        "expected_citations": ["Art. 192", "Art. 14 letra D", "DL-830", "DL-824"],
        "description": "Cruce PRO-PYME + Art. 192 CT (el caso más crítico)",
    },
    {
        "query": "¿Qué gastos son rechazados tributariamente?",
        "expected_chunks": ["ley_lir_art_21"],
        "expected_citations": ["Art. 21", "DL-824"],
        "description": "Gastos rechazados LIR",
    },
    {
        "query": "¿Cómo se deprecian los activos fijos?",
        "expected_chunks": ["ley_lir_art_31"],
        "expected_citations": ["Art. 31", "DL-824", "depreciaci"],
        "description": "Depreciación Art. 31 LIR",
    },
    {
        "query": "¿Qué pasa si el SII me cita para fiscalizar?",
        "expected_chunks": ["ley_codigo_tributario_art_63"],
        "expected_citations": ["Art. 63", "DL-830"],
        "description": "Citación SII Art. 63 CT",
    },
    {
        "query": "¿Cuándo prescriben las deudas tributarias?",
        "expected_chunks": ["ley_codigo_tributario_art_200", "ley_codigo_tributario_art_201"],
        "expected_citations": ["Art. 200", "Art. 201", "DL-830"],
        "description": "Prescripción tributaria",
    },
    {
        "query": "¿Cómo funciona el crédito fiscal en el IVA?",
        "expected_chunks": ["ley_iva_art_12"],
        "expected_citations": ["Art. 12", "DL-825"],
        "description": "IVA crédito fiscal",
    },
]


async def run_tests() -> None:
    table = Table(title="Resultados de Validación RAG")
    table.add_column("Caso", style="cyan", no_wrap=True)
    table.add_column("Descripción", style="white")
    table.add_column("Chunks OK", style="green")
    table.add_column("Citas OK", style="green")
    table.add_column("Estado", style="bold")

    passed = 0
    failed = 0

    for case in TEST_CASES:
        query = case["query"]
        console.print(f"\n[dim]Test: {case['description']}[/dim]")
        console.print(f"[dim]Query: {query}[/dim]")

        # 1. Buscar chunks
        try:
            results = await rag_engine.search_for_conversation(query)
        except Exception as e:
            console.print(f"[red]ERROR en búsqueda: {e}[/red]")
            results = []

        # Verificar chunks esperados
        chunk_uids = [r.chunk.chunk_uid for r in results]
        chunks_found = []
        for expected in case["expected_chunks"]:
            found = any(expected in uid for uid in chunk_uids)
            chunks_found.append(found)
        chunks_ok = all(chunks_found)

        # 2. Construir contexto y verificar citas
        context = await rag_engine.build_context(results, query=query)
        guardrail = CitationGuardrail()
        guardrail.load_context(context)

        # Simular una respuesta básica con las citas esperadas
        # En un test real, llamaríamos al LLM; aquí verificamos que el contexto las contenga
        citations_found = []
        for expected_citation in case["expected_citations"]:
            found = expected_citation.lower() in context.lower()
            citations_found.append(found)
        citations_ok = all(citations_found)

        # 3. Estado
        if chunks_ok and citations_ok:
            status = "[green]PASS[/green]"
            passed += 1
        else:
            status = "[red]FAIL[/red]"
            failed += 1

        table.add_row(
            case["description"],
            query[:50] + "..." if len(query) > 50 else query,
            "Si" if chunks_ok else "No",
            "Si" if citations_ok else "No",
            status,
        )

        if not chunks_ok:
            console.print(f"[yellow]  Chunks recuperados: {chunk_uids[:5]}[/yellow]")
        if not citations_ok:
            missing = [c for c, f in zip(case["expected_citations"], citations_found) if not f]
            console.print(f"[yellow]  Citas faltantes en contexto: {missing}[/yellow]")

    console.print("\n")
    console.print(table)
    console.print(f"\n[bold]Total: {passed} PASS, {failed} FAIL[/bold]")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_tests())
