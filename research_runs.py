"""Trabajos persistentes de investigación profunda y control de presupuesto."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import config
from privacy_guard import redact_for_external
from production_store import store


@dataclass
class ResearchBudget:
    run_id: str
    provider: str

    def reserve(self, purpose: str, *, tokens: int, final_stage: bool = False) -> str:
        # Estimación conservadora por llamada. El proveedor no expone consumo
        # uniforme en todas sus APIs, por lo que se registra la reserva y puede
        # rectificarse al incorporar métricas específicas de cada proveedor.
        estimate = max(0.03, min(2.50, tokens / 10_000 * 0.35))
        return store.reserve_research_usage(
            self.run_id, purpose=purpose, provider=self.provider, estimate_usd=estimate,
            final_stage=final_stage, monthly_limit_usd=config.RESEARCH_MONTHLY_BUDGET_USD,
        )

    def commit(self, reservation_id: str) -> None:
        store.commit_research_usage(reservation_id)

    def release(self, reservation_id: str) -> None:
        store.release_research_usage(reservation_id)


class ResearchRunManager:
    """Mantiene tareas activas; el estado y resultado permanecen en SQLite."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self, *, query: str, client_id: str | None = None, case_id: str | None = None,
                    facts_date: str | None = None, budget_usd: float | None = None) -> dict[str, Any]:
        cleaned = redact_for_external(query).text.strip()
        if not cleaned:
            raise ValueError("Escribe una consulta para investigar.")
        budget = budget_usd if budget_usd is not None else config.RESEARCH_RUN_BUDGET_USD
        if budget <= 0 or budget > config.RESEARCH_RUN_BUDGET_USD:
            raise ValueError(f"El presupuesto por investigación debe estar entre 0 y USD {config.RESEARCH_RUN_BUDGET_USD:.2f}.")
        run = store.create_research_run(query=cleaned, client_id=client_id, case_id=case_id,
                                        facts_date=facts_date, budget_usd=budget)
        self._tasks[run["id"]] = asyncio.create_task(self._execute(run["id"]))
        return store.research_run(run["id"]) or run

    async def resume(self, run_id: str) -> dict[str, Any]:
        run = store.research_run(run_id)
        if not run:
            raise KeyError("Investigación no encontrada")
        task = self._tasks.get(run_id)
        if task and not task.done():
            return run
        store.set_research_run(run_id, status="queued")
        self._tasks[run_id] = asyncio.create_task(self._execute(run_id))
        return store.research_run(run_id) or run

    async def clarify(self, run_id: str, message: str) -> dict[str, Any]:
        """Añade una aclaración seudonimizada y vuelve a ejecutar el mismo expediente."""
        cleaned = redact_for_external(message).text.strip()
        if not cleaned:
            raise ValueError("La aclaración no contiene información investigable tras proteger los datos personales.")
        store.append_research_clarification(run_id, cleaned)
        return await self.resume(run_id)

    async def recover(self) -> int:
        """Reanuda trabajos interrumpidos por un reinicio del servidor."""
        recovered = 0
        for run in store.restartable_research_runs():
            run_id = run["id"]
            task = self._tasks.get(run_id)
            if task and not task.done():
                continue
            store.set_research_run(run_id, status="queued")
            self._tasks[run_id] = asyncio.create_task(self._execute(run_id))
            recovered += 1
        return recovered

    def cancel(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        store.set_research_run(run_id, status="cancelled")

    async def _execute(self, run_id: str) -> None:
        from research_agent import run_research
        budget = ResearchBudget(run_id=run_id, provider=config.RESEARCH_LLM_PROVIDER or "configured")
        try:
            run = store.research_run(run_id)
            if not run:
                return
            store.set_research_run(run_id, status="running")
            result = await run_research(run["query"], cliente=run.get("client_id"),
                                        fecha_hechos=run.get("facts_date"), run_id=run_id,
                                        _budget=budget, include_local_laws=True)
            usage = store.research_usage(run_id)
            spent = round(sum(float(item.get("actual_usd") or item.get("estimated_usd") or 0)
                              for item in usage if item.get("status") != "released"), 4)
            result["scope"] = {
                "facts_date": run.get("facts_date"),
                "research_cutoff": datetime.now(timezone.utc).date().isoformat(),
                "cost_usd": spent,
                "budget_usd": run["budget_usd"],
            }
            state = "completed" if result.get("quality") == "complete" else "partial"
            store.set_research_run(run_id, status=state, result=result)
        except asyncio.CancelledError:
            store.set_research_run(run_id, status="cancelled")
            raise
        except Exception as exc:
            try:
                store.set_research_run(run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            except Exception:
                pass


research_runs = ResearchRunManager()
