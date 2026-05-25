"""
Persistencia de configuración en SQLite.
Compartido entre bot de Telegram y servidor web.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config


class SettingsStore:
    """Persiste configuración de notebooks y estado en SQLite."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or config.TELEGRAM_DB_PATH
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notebook_cache (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_count INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get(self, key: str, default: str = "") -> str:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, now),
            )

    def get_notebooks(self) -> list[dict]:
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT id, name, source_count FROM notebook_cache ORDER BY name"
            ).fetchall()
        return [{"id": r[0], "name": r[1], "source_count": r[2]} for r in rows]

    def save_notebooks(self, notebooks: list[dict]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("DELETE FROM notebook_cache")
            for nb in notebooks:
                conn.execute(
                    "INSERT INTO notebook_cache (id, name, source_count, updated_at) VALUES (?, ?, ?, ?)",
                    (nb.get("id"), nb.get("name"), nb.get("source_count", 0), now),
                )


# Instancia global
store = SettingsStore()

# Inicializar defaults
if not store.get("primary_notebook_name"):
    store.set("primary_notebook_name", config.NOTEBOOKLM_NOTEBOOK_NAME)
