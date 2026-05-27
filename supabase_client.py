"""
Cliente para Supabase.

Usa el cliente síncrono de supabase-py envuelto en run_in_executor
para compatibilidad con async/await del bot.
"""

from __future__ import annotations

import asyncio
from typing import Any

from supabase import Client, create_client

import config


class SupabaseClient:
    """Cliente Supabase con soporte async via thread pool."""

    _instance: SupabaseClient | None = None

    def __new__(cls) -> SupabaseClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._client: Client | None = None
        return cls._instance

    def _ensure_client(self) -> Client:
        if self._client is not None:
            return self._client

        if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
            raise RuntimeError(
                "SUPABASE_URL y SUPABASE_SERVICE_KEY deben estar configurados"
            )

        self._client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
        return self._client

    @property
    def client(self) -> Client:
        return self._ensure_client()

    def table(self, name: str) -> Any:
        """Acceso directo a una tabla."""
        return self._ensure_client().table(name)

    def rpc(self, fn: str, params: dict[str, Any] | None = None) -> Any:
        """Ejecuta una función RPC."""
        return self._ensure_client().rpc(fn, params or {})


# Instancia global
supabase = SupabaseClient()
