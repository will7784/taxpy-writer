"""
Cliente Singleton para Supabase.
"""

from __future__ import annotations

import asyncio
from typing import Any

from supabase import Client, create_client

import config


class SupabaseClient:
    """Singleton thread-safe de cliente Supabase."""

    _instance: SupabaseClient | None = None
    _lock = asyncio.Lock()

    def __new__(cls) -> SupabaseClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._client: Client | None = None
        return cls._instance

    async def _ensure_client(self) -> Client:
        if self._client is not None:
            return self._client

        if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
            raise RuntimeError(
                "SUPABASE_URL y SUPABASE_SERVICE_KEY deben estar configurados en el entorno"
            )

        # create_client es síncrono pero puede hacer I/O; lo envolvemos en thread
        loop = asyncio.get_event_loop()
        self._client = await loop.run_in_executor(
            None,
            lambda: create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY),
        )
        return self._client

    @property
    async def client(self) -> Client:
        return await self._ensure_client()

    async def table(self, name: str) -> Any:
        """Acceso directo a una tabla."""
        client = await self._ensure_client()
        return client.table(name)

    async def rpc(self, fn: str, params: dict[str, Any] | None = None) -> Any:
        """Ejecuta una función RPC."""
        client = await self._ensure_client()
        return client.rpc(fn, params or {})


# Instancia global
supabase = SupabaseClient()
