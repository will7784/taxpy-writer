"""Planificador diario ligero para Railway/uvicorn de una sola instancia."""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import config
from official_sources import sync_official_sources


async def notify_admins(text: str) -> None:
    if not config.TELEGRAM_BOT_TOKEN or not config.ADMIN_TELEGRAM_CHAT_IDS:
        return
    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        for chat_id in config.ADMIN_TELEGRAM_CHAT_IDS:
            try:
                await client.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                                  json={"chat_id": chat_id, "text": text[:4000]})
            except Exception:
                pass


async def run_daily_monitor() -> None:
    """Ejecuta una vez al dia y solo alerta cambios materiales o fallos."""
    hour, minute = (int(x) for x in config.OFFICIAL_SYNC_HOUR.split(":", 1))
    tz = ZoneInfo(config.OFFICIAL_SYNC_TIMEZONE)
    last_run = ""
    while True:
        now = datetime.now(tz); today = now.date().isoformat()
        if now.hour == hour and now.minute == minute and last_run != today:
            last_run = today
            result = await sync_official_sources()
            # El índice general y la sincronización específica del SII se
            # ejecutan en el mismo ciclo: la portada oficial no reemplaza los
            # pronunciamientos ni circulares que ésta anuncia.
            try:
                from scripts.sync_sii import sync_acj, sync_circulares
                from library_import import import_existing_sii_documents
                result["sii_circulares"] = await sync_circulares()
                result["sii_acj"] = await sync_acj(full=False)
                result["sii_import"] = await asyncio.to_thread(import_existing_sii_documents)
            except Exception as exc:
                result["errors"].append(f"SII incremental: {type(exc).__name__}: {exc}")
            if result["errors"]:
                await notify_admins("ImpuestIA: falló la sincronización oficial. Revisa el panel.")
            elif result["changes"]:
                await notify_admins(f"ImpuestIA: {result['changes']} cambio(s) oficial(es) detectado(s). Revisa el panel.")
        await asyncio.sleep(30)
