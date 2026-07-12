"""
Schemas Pydantic — Taxpy.

Usados para (a) el AST del parser legal (legal_parser.py) y (b) salidas
estructuradas de LLM (llm_client.chat_completion_structured, borradores
de árboles de decisión). El resto del repo sigue usando @dataclass
(models.py) para lo que ya persiste tal cual en Supabase; estos modelos
son para datos que necesitan validarse ANTES de llegar ahí.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocType = Literal["ley", "circular", "jurisprudencia"]
NodeType = Literal["articulo", "inciso", "numeral", "letra"]


class LegalNode(BaseModel):
    """Nodo del AST jerárquico de un texto legal (ley/código)."""

    node_type: NodeType
    identifier: str  # "63", "8", "b"
    text: str
    children: list["LegalNode"] = Field(default_factory=list)


LegalNode.model_rebuild()


class DocumentPattern(BaseModel):
    """Patrones regex que definen cómo segmentar un tipo de documento.

    Una ley nueva se agrega registrando una entrada en
    legal_parser.DOCUMENT_PATTERNS, no escribiendo una función nueva.
    """

    doc_type: DocType
    article_patterns: list[str]
    numeral_pattern: str | None = None
    letra_pattern: str | None = None
    # Marca el inicio de "Artículos Transitorios" (renumeran desde 1).
    # Los identificadores de esa sección se prefijan para no chocar con
    # el cuerpo principal (ver legal_parser.LegalParser.parse_articles).
    transitorio_marker: str | None = None


# ── Borradores de árboles de decisión (decision_tree_drafter.py) ────
#
# Espejo Pydantic de DecisionNode/DecisionTree (decision_engine.py) para
# poder pedirle al LLM una salida estructurada validada. Los borradores
# se escriben en decision_trees/_drafts/ — NUNCA directo a
# decision_trees/codigo_tributario/, que solo se llena por aprobación
# humana vía la UI de revisión (web_server.py).

NodeKind = Literal["decision", "info", "result"]


class DraftBranch(BaseModel):
    condition: str
    next_node: str


class DraftNode(BaseModel):
    id: str
    type: NodeKind
    question: str = ""
    help: str = ""
    legal_ref: str = ""
    summary: str = ""
    details: str = ""
    warnings: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    branches: list[DraftBranch] = Field(default_factory=list)
    next_node: str = ""
    legal_refs: list[str] = Field(default_factory=list)


class DraftTree(BaseModel):
    """Borrador completo. `to_file_json()` en decision_tree_drafter.py lo
    convierte al mismo formato JSON que los 10 árboles ya validados
    (root + nodes como dict, no como lista) para que decision_engine.py
    lo pueda cargar sin cambios."""

    tree_id: str
    title: str
    law: str
    article: str
    description: str
    tags: list[str] = Field(default_factory=list)
    root: DraftNode
    nodes: list[DraftNode] = Field(default_factory=list)
