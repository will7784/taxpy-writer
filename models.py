"""
Data models para Taxpy RAG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DocumentChunk:
    """Representa un chunk de documento indexado en Supabase."""

    chunk_uid: str
    source_path: str
    filename: str
    source_type: str
    content: str
    content_hash: str
    embedding: list[float] | None = None
    law_tag: str | None = None
    hierarchy_path: str | None = None
    section_level_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    is_derogada: bool = False
    organization_id: str | None = None
    parent_chunk_uid: str | None = None
    chunk_index: int = 0
    total_chunks: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_db_dict(self) -> dict[str, Any]:
        """Serializa para inserción en Supabase."""
        return {
            "chunk_uid": self.chunk_uid,
            "source_path": self.source_path,
            "filename": self.filename,
            "source_type": self.source_type,
            "law_tag": self.law_tag,
            "hierarchy_path": self.hierarchy_path,
            "section_level_name": self.section_level_name,
            "content": self.content,
            "metadata": self.metadata,
            "content_hash": self.content_hash,
            "is_derogada": self.is_derogada,
            "organization_id": self.organization_id,
            "parent_chunk_uid": self.parent_chunk_uid,
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> DocumentChunk:
        """Construye desde fila de Supabase."""
        return cls(
            chunk_uid=row["chunk_uid"],
            source_path=row["source_path"],
            filename=row["filename"],
            source_type=row["source_type"],
            content=row["content"],
            content_hash=row.get("content_hash", ""),
            law_tag=row.get("law_tag"),
            hierarchy_path=row.get("hierarchy_path"),
            section_level_name=row.get("section_level_name"),
            metadata=row.get("metadata", {}),
            is_derogada=row.get("is_derogada", False),
            organization_id=row.get("organization_id"),
            parent_chunk_uid=row.get("parent_chunk_uid"),
            chunk_index=row.get("chunk_index", 0),
            total_chunks=row.get("total_chunks", 1),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


@dataclass
class SearchResult:
    """Resultado de búsqueda semántica."""

    chunk: DocumentChunk
    similarity: float


@dataclass
class Organization:
    """Organización (bufete, despacho, contador independiente)."""

    id: str
    name: str
    slug: str
    plan_id: str
    created_at: datetime | None = None
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """Plan de suscripción."""

    id: str
    name: str
    max_users: int
    max_documents: int
    max_queries_per_day: int | None
    allows_voice: bool
    allows_pdf_download: bool
    price_monthly_usd: float


@dataclass
class User:
    """Usuario del sistema."""

    id: str
    organization_id: str
    telegram_user_id: int | None = None
    email: str | None = None
    role: str = "member"  # owner, admin, member
    created_at: datetime | None = None
