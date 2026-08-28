"""
Context-RAG: RAG basado en contexto largo (sin chunking, sin embeddings).

Reemplaza rag_engine.py, graph_engine.py y el pipeline de chunking.
"""

from context_rag.law_loader import Law, LawLoader, law_loader
from context_rag.law_map import LawMap, LawMapBuilder, law_map
from context_rag.context_router import RouteDecision, classify_keywords, route_query
from context_rag.prompt_builder import PromptInput, build_context, build_for_chat

__all__ = [
    "Law",
    "LawLoader",
    "law_loader",
    "LawMap",
    "LawMapBuilder",
    "law_map",
    "RouteDecision",
    "classify_keywords",
    "route_query",
    "PromptInput",
    "build_context",
    "build_for_chat",
]
