"""
Pipeline de ingestión de documentos a Supabase pgvector.

Soporta:
- Jurisprudencia judicial SII (.md)
- Circulares SII (.md)
- Leyes chilenas en PDF (LIR, IVA, Código Tributario)
- Documentos internos de organizaciones (PDF, .docx, .md)
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from rich.console import Console

import config
from models import DocumentChunk
from supabase_client import supabase

console = Console()

# Mapeo de cuerpo normativo ID → law_tag
CUERPO_NORMATIVO_MAP = {
    "1": "lir",
    "2": "codigo_tributario",
    "3": "iva",
}

# Nombres de leyes para detectar en PDFs
LAW_TAG_FROM_FILENAME = {
    "dl-824": "lir",
    "dl-825": "iva",
    "dl-830": "codigo_tributario",
    "codigo tributario": "codigo_tributario",
    "impuesto a la renta": "lir",
}


# Límite de tokens de OpenAI para embeddings: 8192
# En español: ~1 token = ~1.3-1.5 caracteres (promedio)
# Usamos un límite conservador de 6000 caracteres ≈ ~4000-4500 tokens
MAX_EMBEDDING_CHARS = 6000


def _truncate_for_embedding(text: str, max_chars: int = MAX_EMBEDDING_CHARS) -> str:
    """Trunca texto para no exceder el límite de tokens de OpenAI embeddings."""
    if len(text) <= max_chars:
        return text
    # Truncar al final de la última oración completa antes del límite
    truncated = text[:max_chars]
    # Buscar el último punto, salto de línea o espacio
    for sep in ["\n\n", ". ", ".\n", "; ", ": ", "\n", " "]:
        last_sep = truncated.rfind(sep)
        if last_sep > max_chars * 0.7:  # Al menos 70% del texto
            return truncated[:last_sep + len(sep)].strip()
    return truncated.strip()


class EmbeddingGenerator:
    """Genera embeddings con OpenAI."""

    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

    async def generate(self, texts: list[str]) -> list[list[float]]:
        """Genera embeddings para una lista de textos."""
        if not texts:
            return []
        # Truncar cada texto antes de enviar
        truncated = [_truncate_for_embedding(t) for t in texts]
        response = await self._client.embeddings.create(
            model=config.OPENAI_EMBEDDING_MODEL,
            input=truncated,
        )
        return [item.embedding for item in response.data]

    async def generate_single(self, text: str) -> list[float]:
        """Genera embedding para un solo texto."""
        truncated = _truncate_for_embedding(text)
        embeddings = await self.generate([truncated])
        return embeddings[0]


class JurisprudenciaMDParser:
    """Parsea archivos .md de jurisprudencia judicial del SII."""

    @staticmethod
    def parse(filepath: Path) -> DocumentChunk | None:
        text = filepath.read_text(encoding="utf-8")

        # Extraer metadatos
        meta: dict[str, str] = {}
        meta_match = re.search(r"## Metadata\n(.*?)(?=\n## )", text, re.DOTALL)
        if meta_match:
            for line in meta_match.group(1).strip().split("\n"):
                line = line.strip().lstrip("- ")
                if ":" in line:
                    key, val = line.split(":", 1)
                    meta[key.strip()] = val.strip()

        # Extraer contenido (Resumen + Contenido)
        content_parts = []
        for section in ["Resumen", "Contenido"]:
            match = re.search(rf"## {section}\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
            if match:
                section_text = match.group(1).strip()
                if section_text and section_text != ".":
                    content_parts.append(f"{section}:\n{section_text}")

        if not content_parts:
            console.print(f"[yellow]⚠️ Sin contenido válido: {filepath}[/yellow]")
            return None

        content = "\n\n".join(content_parts)

        # Determinar law_tag
        cuerpo_id = meta.get("cuerpo_normativo_id", "")
        law_tag = CUERPO_NORMATIVO_MAP.get(cuerpo_id, "codigo_tributario")

        articulo = meta.get("articulo_nombre", "")
        hierarchy = f"{law_tag}/art_{articulo}" if articulo else law_tag

        jurisprudencia_id = meta.get("jurisprudencia_id", "")
        if not jurisprudencia_id:
            # Fallback: extraer del nombre de archivo
            m = re.search(r"sii_pron_(\d+)", filepath.name)
            jurisprudencia_id = m.group(1) if m else filepath.stem

        chunk_uid = f"sii_judicial_{jurisprudencia_id}"
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        return DocumentChunk(
            chunk_uid=chunk_uid,
            source_path=str(filepath),
            filename=filepath.name,
            source_type="jurisprudencia_judicial",
            law_tag=law_tag,
            hierarchy_path=hierarchy,
            section_level_name=meta.get("tipo_pronunciamiento"),
            content=content,
            content_hash=content_hash,
            metadata={
                "jurisprudencia_id": jurisprudencia_id,
                "codigo_pronunciamiento": meta.get("codigo_pronunciamiento", ""),
                "fecha": meta.get("fecha", ""),
                "instancia": meta.get("instancia", ""),
                "tipo_pronunciamiento": meta.get("tipo_pronunciamiento", ""),
                "articulo": articulo,
                "pdf_url": meta.get("pdf_url", ""),
                "pdf_descargado": meta.get("pdf_descargado", "no"),
            },
            is_derogada=False,
        )


class CircularMDParser:
    """Parsea archivos .md de circulares del SII."""

    @staticmethod
    def parse(filepath: Path) -> DocumentChunk | None:
        text = filepath.read_text(encoding="utf-8")

        # Extraer metadatos
        meta: dict[str, str] = {}
        meta_match = re.search(r"## Metadata\n(.*?)(?=\n## )", text, re.DOTALL)
        if meta_match:
            for line in meta_match.group(1).strip().split("\n"):
                line = line.strip().lstrip("- ")
                if ":" in line:
                    key, val = line.split(":", 1)
                    meta[key.strip()] = val.strip()

        # Extraer contenido
        content_parts = []
        for section in ["Resumen", "Contenido"]:
            match = re.search(rf"## {section}\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
            if match:
                section_text = match.group(1).strip()
                if section_text and section_text != ".":
                    content_parts.append(f"{section}:\n{section_text}")

        if not content_parts:
            return None

        content = "\n\n".join(content_parts)

        jurisprudencia_id = meta.get("jurisprudencia_id", "")
        if not jurisprudencia_id:
            m = re.search(r"sii_circular_(\d{4})_(\d+)", filepath.name)
            if m:
                jurisprudencia_id = f"circular-{m.group(1)}-{m.group(2)}"
            else:
                jurisprudencia_id = filepath.stem

        chunk_uid = f"sii_circular_{jurisprudencia_id.replace('-', '_')}"
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        # Detectar law_tag desde el contenido
        law_tag = CircularMDParser._detect_law_tag(content)

        return DocumentChunk(
            chunk_uid=chunk_uid,
            source_path=str(filepath),
            filename=filepath.name,
            source_type="circular",
            law_tag=law_tag,
            hierarchy_path=f"circulares/{meta.get('fecha', '')[:4]}",
            section_level_name="Circular",
            content=content,
            content_hash=content_hash,
            metadata={
                "jurisprudencia_id": jurisprudencia_id,
                "codigo_pronunciamiento": meta.get("codigo_pronunciamiento", ""),
                "fecha": meta.get("fecha", ""),
                "estado_vigencia": meta.get("estado_vigencia", ""),
                "dejada_sin_efecto_por": meta.get("dejada_sin_efecto_por", ""),
                "pdf_url": meta.get("pdf_url", ""),
            },
            is_derogada=meta.get("estado_vigencia", "").lower() in ["derogada", "revocada"],
        )

    @staticmethod
    def _detect_law_tag(content: str) -> str | None:
        """Detecta qué ley regula la circular desde su contenido."""
        content_lower = content.lower()
        if any(x in content_lower for x in ["impuesto a la renta", "renta", "lir", "ley sobre impuesto"]):
            return "lir"
        if any(x in content_lower for x in ["iva", "impuesto al valor agregado", "value added"]):
            return "iva"
        if any(x in content_lower for x in ["código tributario", "codigo tributario", "dl 830"]):
            return "codigo_tributario"
        return None


class PDFLawParser:
    """Parsea PDFs de leyes chilenas y los chunkéa por artículo."""

    @staticmethod
    def parse(filepath: Path) -> list[DocumentChunk]:
        try:
            import pdfplumber
        except ImportError:
            console.print("[red]❌ pdfplumber no está instalado. Instálalo con: pip install pdfplumber[/red]")
            return []

        law_tag = PDFLawParser._detect_law_tag(filepath.name)
        chunks: list[DocumentChunk] = []

        with pdfplumber.open(filepath) as pdf:
            full_text = ""
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"

        if not full_text.strip():
            console.print(f"[yellow]⚠️ PDF vacío o no legible: {filepath}[/yellow]")
            return chunks

        # Dividir por artículos usando regex común en leyes chilenas
        # Patrones: "Artículo 1.", "ARTÍCULO 1.", "Art. 1.", etc.
        article_pattern = re.compile(
            r"(?:^|\n)\s*(?:ART[ÍI]CULO|Art[íi]culo|Art\.?)\s*(\d+[\w\s]*?)\.?\s*(?=\n)",
            re.IGNORECASE,
        )

        splits = list(article_pattern.finditer(full_text))
        if len(splits) < 2:
            # Si no detecta artículos, guardar todo como un solo chunk
            content_hash = hashlib.sha256(full_text.encode()).hexdigest()
            chunks.append(DocumentChunk(
                chunk_uid=f"ley_{law_tag}_full",
                source_path=str(filepath),
                filename=filepath.name,
                source_type="ley",
                law_tag=law_tag,
                hierarchy_path=law_tag,
                content=full_text[:8000],  # Limitar tamaño
                content_hash=content_hash,
                metadata={"tipo": "ley_completa", "filename": filepath.name},
            ))
            return chunks

        for i, match in enumerate(splits):
            start = match.start()
            end = splits[i + 1].start() if i + 1 < len(splits) else len(full_text)
            article_text = full_text[start:end].strip()
            article_num = match.group(1).strip().replace(" ", "_")

            if len(article_text) < 20:
                continue

            chunk_uid = f"ley_{law_tag}_art_{article_num}"
            content_hash = hashlib.sha256(article_text.encode()).hexdigest()

            chunks.append(DocumentChunk(
                chunk_uid=chunk_uid,
                source_path=str(filepath),
                filename=filepath.name,
                source_type="ley",
                law_tag=law_tag,
                hierarchy_path=f"{law_tag}/art_{article_num}",
                section_level_name=f"Artículo {article_num}",
                content=article_text,
                content_hash=content_hash,
                metadata={
                    "tipo": "ley",
                    "articulo": article_num,
                    "filename": filepath.name,
                },
                chunk_index=i,
                total_chunks=len(splits),
            ))

        return chunks

    @staticmethod
    def _detect_law_tag(filename: str) -> str:
        filename_lower = filename.lower()
        for key, tag in LAW_TAG_FROM_FILENAME.items():
            if key in filename_lower:
                return tag
        return "ley"


class IngestionPipeline:
    """Orquesta la ingestión completa de documentos a Supabase."""

    BATCH_SIZE = 20  # Reducido para no exceder 300k tokens de OpenAI
    EMBEDDING_BATCH_SIZE = 15  # Más pequeño para embeddings (límite 300k tokens)

    def __init__(self) -> None:
        self.embedder = EmbeddingGenerator()

    async def ingest_jurisprudencia_judicial(self, base_dir: Path | None = None) -> int:
        """Ingesta todos los .md de jurisprudencia judicial."""
        base_dir = base_dir or (config.DOCUMENTS_DIR / "jurisprudencia_sii")
        files = list(base_dir.rglob("sii_pron_*.md"))
        console.print(f"[blue]📁 Jurisprudencia judicial: {len(files)} archivos encontrados[/blue]")

        chunks: list[DocumentChunk] = []
        for filepath in files:
            chunk = JurisprudenciaMDParser.parse(filepath)
            if chunk:
                chunks.append(chunk)

        await self._upsert_chunks(chunks)
        return len(chunks)

    async def ingest_circulares(self, base_dir: Path | None = None) -> int:
        """Ingesta todos los .md de circulares."""
        base_dir = base_dir or (config.DOCUMENTS_DIR / "jurisprudencia_sii_circulares")
        files = list(base_dir.rglob("sii_circular_*.md"))
        console.print(f"[blue]📁 Circulares: {len(files)} archivos encontrados[/blue]")

        chunks: list[DocumentChunk] = []
        for filepath in files:
            chunk = CircularMDParser.parse(filepath)
            if chunk:
                chunks.append(chunk)

        await self._upsert_chunks(chunks)
        return len(chunks)

    async def ingest_leyes_pdf(self, base_dir: Path | None = None) -> int:
        """Ingesta los PDFs de leyes chilenas."""
        base_dir = base_dir or config.DOCUMENTS_DIR
        # Buscar PDFs en la raíz de documents/
        files = [f for f in base_dir.glob("*.pdf") if any(
            kw in f.name.lower() for kw in ["dl-824", "dl-825", "dl-830", "codigo", "renta", "iva"]
        )]
        console.print(f"[blue]📁 Leyes PDF: {len(files)} archivos encontrados[/blue]")

        chunks: list[DocumentChunk] = []
        for filepath in files:
            file_chunks = PDFLawParser.parse(filepath)
            chunks.extend(file_chunks)

        await self._upsert_chunks(chunks)
        return len(chunks)

    async def ingest_all(self) -> dict[str, int]:
        """Ingesta todo el corpus disponible."""
        results = {}
        results["jurisprudencia_judicial"] = await self.ingest_jurisprudencia_judicial()
        results["circulares"] = await self.ingest_circulares()
        results["leyes_pdf"] = await self.ingest_leyes_pdf()
        total = sum(results.values())
        console.print(f"[green]✅ Total de chunks ingestados: {total}[/green]")
        return results

    async def _upsert_chunks(self, chunks: list[DocumentChunk]) -> None:
        """Inserta o actualiza chunks en Supabase con embeddings, en batches."""
        if not chunks:
            return

        # Verificar duplicados por content_hash
        existing_hashes = await self._get_existing_hashes([c.content_hash for c in chunks])
        new_chunks = [c for c in chunks if c.content_hash not in existing_hashes]

        if not new_chunks:
            console.print(f"[dim]⏭️  Todos los {len(chunks)} chunks ya existen, saltando[/dim]")
            return

        console.print(f"[blue]🔄 Ingestando {len(new_chunks)} chunks nuevos (de {len(chunks)} totales)...[/blue]")

        # Procesar en batches pequeños para embeddings (límite 300k tokens de OpenAI)
        for i in range(0, len(new_chunks), self.EMBEDDING_BATCH_SIZE):
            batch = new_chunks[i : i + self.EMBEDDING_BATCH_SIZE]

            # Generar embeddings con retry
            texts = [c.content for c in batch]
            try:
                embeddings = await self.embedder.generate(texts)
            except Exception as e:
                error_msg = str(e).lower()
                if "max_tokens_per_request" in error_msg or "400" in error_msg:
                    # Reducir aún más el batch y reintentar chunk por chunk
                    console.print(f"[yellow]  ⚠️ Batch muy grande, dividiendo...[/yellow]")
                    embeddings = []
                    for chunk in batch:
                        try:
                            emb = await self.embedder.generate_single(chunk.content)
                            embeddings.append(emb)
                        except Exception as e2:
                            console.print(f"[red]  ✗ Error en chunk {chunk.chunk_uid}: {e2}[/red]")
                            continue
                else:
                    raise

            # Preparar datos para insert
            records = []
            for chunk, emb in zip(batch, embeddings):
                if len(emb) != 1536:
                    console.print(f"[yellow]  ⚠️ Embedding inválido para {chunk.chunk_uid}, saltando[/yellow]")
                    continue
                record = chunk.to_db_dict()
                record["embedding"] = emb
                records.append(record)

            if not records:
                continue

            # Deduplicar por chunk_uid (PostgreSQL no permite duplicados en un solo upsert)
            seen_uids: set[str] = set()
            deduplicated_records = []
            for record in records:
                uid = record["chunk_uid"]
                if uid not in seen_uids:
                    seen_uids.add(uid)
                    deduplicated_records.append(record)

            skipped = len(records) - len(deduplicated_records)
            if skipped > 0:
                console.print(f"[yellow]  ⚠️ {skipped} chunks duplicados omitidos en batch[/yellow]")

            # Insertar en Supabase
            try:
                tbl = await supabase.table("document_chunks")
                result = tbl.upsert(deduplicated_records, on_conflict="chunk_uid").execute()
                console.print(f"[green]  ✓ Batch {i//self.EMBEDDING_BATCH_SIZE + 1}: {len(deduplicated_records)} chunks[/green]")
            except Exception as e:
                console.print(f"[red]  ✗ Error en batch {i//self.EMBEDDING_BATCH_SIZE + 1}: {e}[/red]")

    async def _get_existing_hashes(self, hashes: list[str]) -> set[str]:
        """Consulta qué content_hash ya existen en la base."""
        if not hashes:
            return set()

        # Supabase no soporta IN con listas grandes fácilmente via PostgREST
        # Hacemos la consulta en batches de 100
        existing: set[str] = set()
        for i in range(0, len(hashes), 100):
            batch_hashes = hashes[i : i + 100]
            try:
                tbl = await supabase.table("document_chunks")
                result = tbl.select("content_hash").in_("content_hash", batch_hashes).execute()
                if result.data:
                    existing.update(r["content_hash"] for r in result.data)
            except Exception as e:
                console.print(f"[yellow]⚠️ Error consultando hashes existentes: {e}[/yellow]")

        return existing


async def main() -> None:
    """CLI para ejecutar ingestión manual."""
    pipeline = IngestionPipeline()
    results = await pipeline.ingest_all()
    for key, count in results.items():
        console.print(f"  • {key}: {count} chunks")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
