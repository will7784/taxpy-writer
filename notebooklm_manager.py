"""
Integración con NotebookLM vía notebooklm-py (unofficial Python API).

Requiere autenticación OAuth one-time vía browser (notebooklm-login).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console()

# Intentar importar notebooklm-py; si no está instalado, dar instrucciones
try:
    from notebooklm import NotebookLMClient
    _NOTEBOOKLM_AVAILABLE = True
except Exception:
    _NOTEBOOKLM_AVAILABLE = False
    NotebookLMClient = None  # type: ignore[misc,assignment]


class NotebookLMManager:
    def __init__(
        self,
        notebook_name: str = "Taxpy Jurisprudencia",
        state_path: Path | None = None,
    ) -> None:
        self.notebook_name = notebook_name
        self.state_path = state_path or Path("notebooklm_state.json")
        self._state = self._load_state()

        if not _NOTEBOOKLM_AVAILABLE:
            raise RuntimeError(
                "notebooklm-py no está instalado. "
                "Ejecuta: pip install notebooklm-py"
            )

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"version": 1, "notebooks": {}, "sources": {}}

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def create_or_get_notebook(self) -> str:
        """Crea o recupera el notebook de Taxpy. Evita duplicados y limpia notebooks vacíos."""
        target_name = self.notebook_name.strip().lower()

        # 1. Validar notebook guardado localmente
        local_id = None
        existing = self._state.get("notebooks", {}).get(self.notebook_name)
        if existing and existing.get("id"):
            local_id = existing["id"]
            try:
                async with await NotebookLMClient.from_storage() as client:
                    nb = await client.notebooks.get(local_id)
                    if nb and nb.sources_count > 0:
                        console.print(f"[green]✅ Notebook local válido: {local_id} ({nb.sources_count} fuentes)[/green]")
                        return local_id
                    else:
                        console.print(f"[yellow]⚠️ Notebook local tiene 0 fuentes o no existe: {local_id}[/yellow]")
            except Exception as e:
                console.print(f"[yellow]⚠️ Notebook local no accesible: {e}[/yellow]")
                local_id = None

        # 2. Buscar en NotebookLM por nombre (evitar crear duplicados)
        console.print(f"[blue]🔍 Buscando notebook '{self.notebook_name}' en NotebookLM...[/blue]")
        best_id: str | None = None
        best_sources = -1
        duplicates_to_delete: list[str] = []

        try:
            async with await NotebookLMClient.from_storage() as client:
                notebooks = await client.notebooks.list()
                for nb in notebooks:
                    nb_name = (nb.title or "").strip().lower()
                    if nb_name == target_name:
                        nb_id = str(nb.id)
                        src_count = nb.sources_count
                        if src_count > best_sources:
                            if best_id is not None:
                                duplicates_to_delete.append(best_id)
                            best_id = nb_id
                            best_sources = src_count
                        else:
                            duplicates_to_delete.append(nb_id)
        except Exception as e:
            console.print(f"[yellow]⚠️ Error listando notebooks: {e}[/yellow]")

        # 3. Eliminar duplicados vacíos SOLO si existe un candidato con fuentes
        # (evitar borrar notebooks que el usuario pueda querer mantener vacíos intencionalmente)
        if duplicates_to_delete and best_sources > 0:
            console.print(f"[yellow]🗑️ Eliminando {len(duplicates_to_delete)} notebook(s) duplicado(s) vacío(s)...[/yellow]")
            try:
                async with await NotebookLMClient.from_storage() as client:
                    for dup_id in duplicates_to_delete:
                        try:
                            await client.notebooks.delete(dup_id)
                            console.print(f"  [dim]🗑️ Eliminado duplicado: {dup_id}[/dim]")
                        except Exception as e:
                            console.print(f"  [dim]⚠️ No se pudo eliminar {dup_id}: {e}[/dim]")
            except Exception as e:
                console.print(f"[yellow]⚠️ Error eliminando duplicados: {e}[/yellow]")

        # 4. Si encontramos uno válido por nombre, usarlo
        if best_id and best_sources > 0:
            console.print(f"[green]✅ Notebook encontrado por nombre: {best_id} ({best_sources} fuentes)[/green]")
            self._state.setdefault("notebooks", {})[self.notebook_name] = {
                "id": best_id,
                "created_at": __import__("datetime").datetime.now().isoformat(),
            }
            self._save_state()
            return best_id

        # 5. Crear nuevo solo si no hay ninguno válido
        console.print(f"[blue]📓 Creando notebook '{self.notebook_name}'...[/blue]")
        async with await NotebookLMClient.from_storage() as client:
            nb = await client.notebooks.create(self.notebook_name)
            self._state.setdefault("notebooks", {})[self.notebook_name] = {
                "id": str(nb.id),
                "created_at": __import__("datetime").datetime.now().isoformat(),
            }
            self._save_state()
            console.print(f"[green]✅ Notebook creado: {nb.id}[/green]")
            return str(nb.id)

    async def upload_sources(
        self,
        notebook_id: str,
        pdf_paths: list[Path],
    ) -> dict[str, int]:
        """Sube PDFs como fuentes al notebook. Evita duplicados por hash."""
        stats = {"uploaded": 0, "skipped": 0, "errors": 0}

        async with await NotebookLMClient.from_storage() as client:
            for pdf_path in pdf_paths:
                if not pdf_path.exists():
                    console.print(f"[yellow]⚠️ No existe: {pdf_path}[/yellow]")
                    stats["errors"] += 1
                    continue

                file_hash = self._file_hash(pdf_path)
                source_key = f"{notebook_id}::{file_hash}"

                if source_key in self._state.get("sources", {}):
                    console.print(f"  [dim]⏭️ Ya subido: {pdf_path.name}[/dim]")
                    stats["skipped"] += 1
                    continue

                console.print(f"  [blue]⬆️ Subiendo {pdf_path.name}...[/blue]")
                try:
                    source = await client.sources.add_file(
                        notebook_id,
                        str(pdf_path),
                        wait=True,
                    )
                    self._state.setdefault("sources", {})[source_key] = {
                        "filename": pdf_path.name,
                        "path": str(pdf_path),
                        "source_id": str(source.id) if hasattr(source, "id") else "",
                        "uploaded_at": __import__("datetime").datetime.now().isoformat(),
                    }
                    self._save_state()
                    stats["uploaded"] += 1
                except Exception as e:
                    console.print(f"    [red]❌ Error subiendo {pdf_path.name}: {e}[/red]")
                    stats["errors"] += 1

        return stats

    async def ask_question(
        self,
        notebook_id: str,
        question: str,
    ) -> dict[str, Any]:
        """Hace una pregunta al notebook y retorna respuesta con citas."""
        import asyncio
        console.print(f"[dim]🔍 Preguntando a NotebookLM...[/dim]")
        async with await NotebookLMClient.from_storage() as client:
            result = await asyncio.wait_for(
                client.chat.ask(notebook_id, question),
                timeout=30.0,
            )
            return {
                "answer": result.answer if hasattr(result, "answer") else str(result),
                "citations": [],  # TODO: extraer citas si la API las expone
            }

    async def generate_audio_overview(
        self,
        notebook_id: str,
        instructions: str = "",
    ) -> str:
        """Genera un Audio Overview (podcast) del notebook."""
        console.print(f"[blue]🎙️ Generando Audio Overview...[/blue]")
        async with await NotebookLMClient.from_storage() as client:
            status = await client.artifacts.generate_audio(
                notebook_id,
                instructions=instructions or "Genera un resumen en español",
            )
            await client.artifacts.wait_for_completion(notebook_id, status.task_id)
            return str(status.task_id)

    @staticmethod
    def _file_hash(path: Path) -> str:
        import hashlib

        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    async def list_notebooks(self) -> list[dict[str, Any]]:
        """Lista los notebooks disponibles en la cuenta."""
        import asyncio
        async with await NotebookLMClient.from_storage() as client:
            notebooks = await asyncio.wait_for(client.notebooks.list(), timeout=30.0)
            result = []
            for nb in notebooks:
                # notebooklm-py usa atributos variables para el nombre
                name = getattr(nb, "name", None) or getattr(nb, "title", None) or getattr(nb, "display_name", None) or getattr(nb, "label", None) or "Sin nombre"
                result.append({
                    "id": str(nb.id),
                    "name": str(name),
                    "created_at": getattr(nb, "created_at", ""),
                })
            return result

    async def get_notebook_sources(self, notebook_id: str) -> list[dict[str, Any]]:
        """Lista las fuentes de un notebook específico."""
        import asyncio
        async with await NotebookLMClient.from_storage() as client:
            sources = await asyncio.wait_for(client.sources.list(notebook_id), timeout=30.0)
            return [
                {
                    "id": str(s.id),
                    "name": getattr(s, "name", "Sin nombre"),
                    "type": getattr(s, "type", "desconocido"),
                }
                for s in sources
            ]

    async def save_note(self, notebook_id: str, title: str, content: str) -> str:
        """Guarda una nota en el notebook. Si ya existe una con el mismo título, la actualiza."""
        async with await NotebookLMClient.from_storage() as client:
            notes = await client.notes.list(notebook_id)
            for note in notes:
                if note.title == title:
                    await client.notes.update(notebook_id, note.id, content, title)
                    return note.id
            new_note = await client.notes.create(notebook_id, title, content)
            return new_note.id

    async def delete_note(self, notebook_id: str, note_id: str) -> bool:
        """Elimina una nota del notebook."""
        async with await NotebookLMClient.from_storage() as client:
            return await client.notes.delete(notebook_id, note_id)

    def list_local_sources(self) -> list[dict[str, Any]]:
        return list(self._state.get("sources", {}).values())
