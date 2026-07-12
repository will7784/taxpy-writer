"""
Asistente de árboles de decisión: el LLM propone un borrador, un humano
lo aprueba. Nunca escribe directo a decision_trees/codigo_tributario/ —
solo a decision_trees/_drafts/, que se revisa vía la UI en web_server.py
(rutas /review/*).

Los 10 árboles ya validados (decision_trees/codigo_tributario/, ver
VALIDACION_ARBOLES.md) se escribieron y verificaron artículo por artículo
a mano. Esto no reemplaza ese trabajo — genera el primer borrador para
que la revisión legal parta de algo, no de una hoja en blanco.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import config
from llm_client import LLMClient
from models import DocumentChunk
from schemas import DraftNode, DraftTree

DRAFTS_DIR = Path(config.BASE_DIR) / "decision_trees" / "_drafts"

_SYSTEM_PROMPT = (
    "Eres un asistente que redacta BORRADORES de árboles de decisión jurídica "
    "para un bot de asesoría tributaria chilena, a partir del texto de un "
    "artículo legal. Un abogado va a revisar y corregir cada borrador antes "
    "de publicarlo: tu trabajo es dar un primer punto de partida razonable, "
    "no una respuesta final.\n\n"
    "Sigue el mismo estilo que los árboles ya validados del proyecto:\n"
    "- El nodo root hace UNA pregunta sí/no clara sobre si la situación del "
    "artículo aplica al usuario.\n"
    "- Nodos 'decision' tienen una pregunta y branches (condition -> next_node) "
    "mutuamente excluyentes.\n"
    "- Nodos 'info' explican algo y avanzan solos (next_node).\n"
    "- Nodos 'result' son hojas: summary (1-2 párrafos), details (3-5 párrafos), "
    "warnings (al menos 1 advertencia clave), next_steps (acciones concretas), "
    "legal_refs (referencias exactas: artículo, inciso, numeral).\n"
    "- Cada nodo lleva su legal_ref exacto. NO inventes artículos, incisos ni "
    "numerales que no estén en el texto proporcionado.\n"
    "- Todo nodo 'decision' que no sea el root debe existir dentro de la lista "
    "`nodes`, con su `id` único y consistente con los `next_node` que apuntan a él."
)


def _suggest_tree_id(chunk: DocumentChunk) -> str:
    base = chunk.chunk_uid.replace("ley_", "").replace("_art_", "_")
    return re.sub(r"[^a-z0-9_]", "", base.lower())


async def draft_tree_from_chunk(chunk: DocumentChunk, llm_client: LLMClient) -> DraftTree:
    """Pide al LLM un borrador de árbol de decisión a partir de un chunk ya ingestado."""
    user_prompt = (
        f"Ley/código: {chunk.law_tag}\n"
        f"Sección: {chunk.section_level_name or chunk.hierarchy_path}\n\n"
        f"Texto del artículo:\n{chunk.content}\n\n"
        f"tree_id sugerido: {_suggest_tree_id(chunk)}"
    )
    return await llm_client.chat_completion_structured(
        schema=DraftTree,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=3000,
    )


def _node_to_file_dict(node: DraftNode, *, include_id: bool) -> dict:
    d: dict = {"type": node.type}
    if include_id:
        d["id"] = node.id
    if node.question:
        d["question"] = node.question
    if node.help:
        d["help"] = node.help
    if node.legal_ref:
        d["legal_ref"] = node.legal_ref
    if node.summary:
        d["summary"] = node.summary
    if node.details:
        d["details"] = node.details
    if node.warnings:
        d["warnings"] = node.warnings
    if node.next_steps:
        d["next_steps"] = node.next_steps
    if node.branches:
        d["branches"] = [b.model_dump() for b in node.branches]
    if node.next_node:
        d["next_node"] = node.next_node
    if node.legal_refs:
        d["legal_refs"] = node.legal_refs
    return d


def to_file_json(tree: DraftTree) -> dict:
    """Convierte el borrador al mismo formato que los árboles ya validados
    (root + nodes como dict), directamente cargable por decision_engine.py."""
    return {
        "tree_id": tree.tree_id,
        "title": tree.title,
        "law": tree.law,
        "article": tree.article,
        "description": tree.description,
        "tags": tree.tags,
        "root": _node_to_file_dict(tree.root, include_id=True),
        "nodes": {n.id: _node_to_file_dict(n, include_id=False) for n in tree.nodes},
    }


def save_draft(tree: DraftTree) -> Path:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    path = DRAFTS_DIR / f"{tree.tree_id}.json"
    path.write_text(json.dumps(to_file_json(tree), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


_SHAPES = {"decision": ("{", "}"), "info": ("(", ")"), "result": ("([", "])")}


def _mermaid_label(node_id: str, node: dict) -> str:
    kind = node.get("type", "info")
    text = (node.get("question") or node.get("summary") or node_id)[:60]
    text = text.replace('"', "'").replace("\n", " ")
    opening, closing = _SHAPES.get(kind, ("(", ")"))
    return f'{node_id}{opening}"{text}"{closing}'


def to_mermaid(tree_json: dict) -> str:
    """Genera un diagrama Mermaid flowchart a partir del JSON de un árbol
    (mismo formato de archivo que decision_trees/codigo_tributario/*.json).
    Usado en la UI de revisión (/review/drafts/{tree_id}) para que un
    humano vea el árbol sin tener que leer el JSON crudo."""
    root = tree_json.get("root", {})
    root_id = root.get("id", "root")
    nodes = tree_json.get("nodes", {})

    lines = ["flowchart TD", f"    {_mermaid_label(root_id, root)}"]
    for nid, node in nodes.items():
        lines.append(f"    {_mermaid_label(nid, node)}")

    def emit_edges(nid: str, node: dict) -> None:
        for branch in node.get("branches", []):
            target = branch.get("next_node", "")
            if not target:
                continue
            cond = branch.get("condition", "")[:24].replace('"', "'")
            lines.append(f"    {nid} -->|{cond}| {target}")
        if not node.get("branches") and node.get("next_node"):
            lines.append(f"    {nid} --> {node['next_node']}")

    emit_edges(root_id, root)
    for nid, node in nodes.items():
        emit_edges(nid, node)

    return "\n".join(lines)
