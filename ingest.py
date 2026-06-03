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
from critical_relations import get_critical_relations
from graph_engine import graph as graph_engine
from graph_extractor import GraphExtractor
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
# En español legal: ~1 token = ~1.2-1.4 caracteres (promedio, palabras más largas)
# 10000 caracteres ≈ ~7000-8300 tokens, dentro del límite seguro de 8192
MAX_EMBEDDING_CHARS = 10000


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
    """Parsea PDFs de leyes chilenas y los chunkéa por artículo/modificación."""

    # Regex estricto: solo detecta inicios de artículo/modificación.
    # NO detecta referencias internas tipo "artículo 58 número 3)" en medio de oración.
    ARTICLE_START_RE = re.compile(
        r"""
        ^\s*
        (?:
            ART[ÍI]CULO\s+\d+[°\w]*\s*[\.\-]
          | Art[íi]culo\s+\d+[°\w]*\s*[\.\-]
          | Art\.\s+(?:\d+[°\w]*|segundo|único|primero|tercero|cuarto|quinto|sexto|séptimo|octavo|noveno|décimo)\b
        )
        """,
        re.VERBOSE | re.MULTILINE,
    )

    # Líneas boilerplate de leychile.cl que no aportan valor semántico
    _BOILERPLATE_RE = re.compile(
        r"Biblioteca del Congreso Nacional de Chile - www\.leychile\.cl - documento generado el .*?\n",
        re.IGNORECASE,
    )
    _DECRETO_HEADER_RE = re.compile(
        r"^Decreto Ley \d+, HACIENDA \(\d{4}\)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    _PAGE_NUM_RE = re.compile(
        r"página \d+ de \d+\s*\n",
        re.IGNORECASE,
    )

    @staticmethod
    def _clean_law_text(text: str) -> str:
        """Limpia headers y metadata repetitiva de leychile.cl."""
        text = PDFLawParser._BOILERPLATE_RE.sub("", text)
        text = PDFLawParser._DECRETO_HEADER_RE.sub("", text)
        text = PDFLawParser._PAGE_NUM_RE.sub("", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _extract_article_id(header: str) -> str:
        """Extrae el identificador base del artículo para el UID."""
        header = header.strip()
        m = re.search(r"(?:ART[ÍI]CULO|Art[íi]culo|Art\.)\s+(\S+)", header)
        if not m:
            return "unknown"
        num = m.group(1)
        # Limpiar sufijos de puntuación (°, º, ., -)
        num = re.sub(r"[°º\.\-]+$", "", num)
        return num.lower()

    # Regex para detectar incisos/letras/números dentro de un artículo
    _INCISO_RE = re.compile(
        r"(?:^|\n)\s*"
        r"(?:"
        r"[a-z]\)"           # a) b) c)
        r"|[a-z]\."          # a. b. c.
        r"|\d+\)"            # 1) 2) 3)
        r"|\d+\."            # 1. 2. 3.
        r"|[ivxlc]+\)"       # i) ii) iii) (romanos)
        r"|[IVXLC]+\."      # I. II. III.
        r")",
        re.VERBOSE | re.MULTILINE,
    )

    @staticmethod
    def _split_article_subchunks(article_text: str, header: str, base_uid: str, law_tag: str, filepath: Path, article_num: str, chunk_index: int) -> list[DocumentChunk]:
        """Divide un artículo largo en sub-chunks por incisos para embeddings más precisos."""
        subchunks: list[DocumentChunk] = []
        # Primero, crear el chunk PADRE con el artículo completo
        parent_uid = base_uid
        content_hash = hashlib.sha256(article_text.encode()).hexdigest()
        subchunks.append(DocumentChunk(
            chunk_uid=parent_uid,
            source_path=str(filepath),
            filename=filepath.name,
            source_type="ley",
            law_tag=law_tag,
            hierarchy_path=f"{law_tag}/art_{article_num}",
            section_level_name=header,
            content=article_text,
            content_hash=content_hash,
            metadata={
                "tipo": "ley",
                "articulo": article_num,
                "articulo_header": header,
                "filename": filepath.name,
                "chunk_role": "parent",
            },
            chunk_index=chunk_index,
            total_chunks=1,
        ))

        # Si el artículo es corto, no crear sub-chunks
        if len(article_text) < 3500:
            return subchunks

        # Detectar incisos
        inciso_matches = list(PDFLawParser._INCISO_RE.finditer(article_text))
        if len(inciso_matches) < 2:
            # No hay estructura de incisos clara: usar sliding window
            window_size = 3000
            overlap = 500
            start_positions = list(range(0, len(article_text), window_size - overlap))
            for idx, pos in enumerate(start_positions):
                window_text = article_text[pos:pos + window_size]
                if len(window_text.strip()) < 100:
                    continue
                sub_text = f"{header}\n{window_text.strip()}"
                sub_uid = f"{base_uid}_sub_{idx}"
                sub_hash = hashlib.sha256(sub_text.encode()).hexdigest()
                subchunks.append(DocumentChunk(
                    chunk_uid=sub_uid,
                    source_path=str(filepath),
                    filename=filepath.name,
                    source_type="ley",
                    law_tag=law_tag,
                    hierarchy_path=f"{law_tag}/art_{article_num}",
                    section_level_name=header,
                    content=sub_text,
                    content_hash=sub_hash,
                    parent_chunk_uid=parent_uid,
                    metadata={
                        "tipo": "ley",
                        "articulo": article_num,
                        "articulo_header": header,
                        "filename": filepath.name,
                        "chunk_role": "sub_window",
                    },
                    chunk_index=idx,
                    total_chunks=len(start_positions),
                ))
            return subchunks

        # Dividir por incisos detectados
        for idx, inc_match in enumerate(inciso_matches):
            start = inc_match.start()
            end = inciso_matches[idx + 1].start() if idx + 1 < len(inciso_matches) else len(article_text)
            inciso_text = article_text[start:end].strip()
            if len(inciso_text) < 20:
                continue
            # Prepend el header del artículo para que el embedding tenga contexto
            sub_text = f"{header}\n{inciso_text}"
            sub_uid = f"{base_uid}_sub_{idx}"
            sub_hash = hashlib.sha256(sub_text.encode()).hexdigest()
            subchunks.append(DocumentChunk(
                chunk_uid=sub_uid,
                source_path=str(filepath),
                filename=filepath.name,
                source_type="ley",
                law_tag=law_tag,
                hierarchy_path=f"{law_tag}/art_{article_num}",
                section_level_name=header,
                content=sub_text,
                content_hash=sub_hash,
                parent_chunk_uid=parent_uid,
                metadata={
                    "tipo": "ley",
                    "articulo": article_num,
                    "articulo_header": header,
                    "filename": filepath.name,
                    "chunk_role": "sub_inciso",
                    "inciso_idx": idx,
                },
                chunk_index=idx,
                total_chunks=len(inciso_matches),
            ))

        return subchunks

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

        # Limpiar texto de boilerplate
        full_text = PDFLawParser._clean_law_text(full_text)

        # Detectar inicios de artículo/modificación
        matches = list(PDFLawParser.ARTICLE_START_RE.finditer(full_text))
        if len(matches) < 2:
            # Fallback: guardar todo como un solo chunk
            content_hash = hashlib.sha256(full_text.encode()).hexdigest()
            chunks.append(DocumentChunk(
                chunk_uid=f"ley_{law_tag}_full",
                source_path=str(filepath),
                filename=filepath.name,
                source_type="ley",
                law_tag=law_tag,
                hierarchy_path=law_tag,
                content=full_text[:MAX_EMBEDDING_CHARS],
                content_hash=content_hash,
                metadata={"tipo": "ley_completa", "filename": filepath.name},
            ))
            return chunks

        occurrence_counter: dict[str, int] = {}

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
            article_text = full_text[start:end].strip()

            if len(article_text) < 15:
                continue

            header = match.group().strip()
            article_num = PDFLawParser._extract_article_id(header)

            # UID único: si hay múltiples ocurrencias del mismo artículo,
            # agregamos un índice (modificaciones posteriores)
            base_uid = f"ley_{law_tag}_art_{article_num}"
            occ = occurrence_counter.get(base_uid, 0)
            occurrence_counter[base_uid] = occ + 1
            chunk_uid = f"{base_uid}_{occ}" if occ > 0 else base_uid

            # ── Chunking jerárquico: artículo completo + sub-chunks ──
            article_chunks = PDFLawParser._split_article_subchunks(
                article_text=article_text,
                header=header,
                base_uid=chunk_uid,
                law_tag=law_tag,
                filepath=filepath,
                article_num=article_num,
                chunk_index=i,
            )
            chunks.extend(article_chunks)

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
        self._graph_extractor = GraphExtractor()
        # Insertar relaciones críticas hardcodeadas al iniciar
        self._init_critical_relations()

    def _init_critical_relations(self) -> None:
        """Inserta las relaciones críticas conocidas en el grafo."""
        relations = get_critical_relations()
        if relations:
            try:
                import asyncio
                asyncio.create_task(graph_engine.insert_relations(relations))
                console.print("[dim]🔗 Relaciones críticas del grafo inicializadas[/dim]")
            except Exception:
                pass  # Si el grafo no está listo, se intentará más tarde

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
                result = supabase.table("document_chunks").upsert(deduplicated_records, on_conflict="chunk_uid").execute()
                console.print(f"[green]  ✓ Batch {i//self.EMBEDDING_BATCH_SIZE + 1}: {len(deduplicated_records)} chunks[/green]")
            except Exception as e:
                console.print(f"[red]  ✗ Error en batch {i//self.EMBEDDING_BATCH_SIZE + 1}: {e}[/red]")

        # ── GraphRAG: extraer relaciones de los chunks nuevos ──
        if new_chunks:
            await self._extract_graph_relations(new_chunks)

    async def _extract_graph_relations(self, chunks: list[DocumentChunk]) -> None:
        """Extrae relaciones legales automáticamente e inserta en knowledge_graph."""
        console.print(f"[blue]🕸️  Extrayendo relaciones del grafo para {len(chunks)} chunks...[/blue]")
        try:
            relations = await self._graph_extractor.extract_relations_batch(chunks)
            if relations:
                inserted = await graph_engine.insert_relations(relations)
                console.print(f"[green]  ✓ {inserted} relaciones insertadas en knowledge_graph[/green]")
            else:
                console.print("[dim]  ⏭️  No se encontraron relaciones nuevas[/dim]")
        except Exception as e:
            console.print(f"[yellow]  ⚠️ Error extrayendo relaciones: {e}[/yellow]")

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
                result = supabase.table("document_chunks").select("content_hash").in_("content_hash", batch_hashes).execute()
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
