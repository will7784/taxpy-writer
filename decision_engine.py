"""
Motor de Árboles de Decisión Jurídica (Tax Decision Engine).

Reemplaza al flujo writer/RAG para temas que tienen un árbol validado.
Flujo:
    1. El Query Interpreter (LLM) clasifica la intención del usuario.
    2. El Decision Engine carga el árbol correspondiente.
    3. Recorre el árbol haciendo preguntas o llegando a un resultado.
    4. Renderiza la respuesta prevalidada + diagrama del camino.

Un árbol de decisión es un JSON con nodos de tipo:
    - decision: pregunta con ramas condicionales
    - info: nodo informativo (sin elección, avanza automático)
    - result: nodo hoja con la respuesta final prevalidada

Cada nodo lleva:
    - legal_ref: referencia exacta al artículo/ley
    - help: contexto para el usuario no abogado
    - template: texto prevalidado (NO lo genera un LLM)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console()


@dataclass
class DecisionNode:
    """Un nodo del árbol de decisión."""

    id: str
    type: str  # "decision" | "info" | "result"
    question: str = ""
    help_text: str = ""
    legal_ref: str = ""
    summary: str = ""
    details: str = ""
    warnings: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    branches: list[dict[str, Any]] = field(default_factory=list)
    # Para nodos info que avanzan automáticamente
    next_node_id: str = ""
    # Referencias legales múltiples (para nodos resultado)
    legal_refs: list[str] = field(default_factory=list)


@dataclass
class DecisionTree:
    """Árbol de decisión completo para un tema jurídico."""

    tree_id: str
    title: str
    law: str
    article: str
    description: str
    root: DecisionNode
    nodes: dict[str, DecisionNode]
    tags: list[str] = field(default_factory=list)


class DecisionEngine:
    """
    Motor que carga árboles JSON y los recorre.

    Usage:
        engine = DecisionEngine()
        tree = engine.find_tree("citacion sii")
        if tree:
            result, path = engine.walk_tree(tree, {"citacion": True, "escrita": True})
    """

    def __init__(self, trees_dir: str = "decision_trees") -> None:
        self._trees_dir = Path(trees_dir)
        self._trees: dict[str, DecisionTree] = {}
        self._index: dict[str, str] = {}  # keyword -> tree_id
        self._load_all_trees()

    # ── Carga de árboles ────────────────────────────────────────

    @staticmethod
    def _normalize(text: str) -> str:
        """Elimina tildes para matching robusto."""
        import unicodedata
        return "".join(
            c for c in unicodedata.normalize("NFD", text.lower())
            if unicodedata.category(c) != "Mn"
        )

    def _load_all_trees(self) -> None:
        if not self._trees_dir.exists():
            console.print(f"[yellow][WARN] No existe {self._trees_dir}[/yellow]")
            return

        for json_file in sorted(self._trees_dir.rglob("*.json")):
            try:
                tree = self._parse_tree(json_file)
                self._trees[tree.tree_id] = tree
                # Indexar por palabras clave (normalizadas, sin tildes)
                for tag in tree.tags:
                    self._index[self._normalize(tag)] = tree.tree_id
                for word in tree.title.lower().split():
                    if len(word) > 3:
                        self._index[self._normalize(word)] = tree.tree_id
                console.print(f"[dim][TREE] {tree.tree_id}: {tree.title}[/dim]")
            except Exception as e:
                console.print(f"[red][ERR] Falló {json_file}: {e}[/red]")

    def _parse_tree(self, path: Path) -> DecisionTree:
        data = json.loads(path.read_text(encoding="utf-8"))
        nodes: dict[str, DecisionNode] = {}
        for nid, n in data.get("nodes", {}).items():
            nodes[nid] = DecisionNode(
                id=nid,
                type=n.get("type", "decision"),
                question=n.get("question", ""),
                help_text=n.get("help", ""),
                legal_ref=n.get("legal_ref", ""),
                summary=n.get("summary", ""),
                details=n.get("details", ""),
                warnings=n.get("warnings", []),
                next_steps=n.get("next_steps", []),
                branches=n.get("branches", []),
                next_node_id=n.get("next_node", ""),
                legal_refs=n.get("legal_refs", []),
            )
        root_data = data["root"]
        root = DecisionNode(
            id=root_data.get("id", "root"),
            type=root_data.get("type", "decision"),
            question=root_data.get("question", ""),
            help_text=root_data.get("help", ""),
            legal_ref=root_data.get("legal_ref", ""),
            summary=root_data.get("summary", ""),
            details=root_data.get("details", ""),
            warnings=root_data.get("warnings", []),
            next_steps=root_data.get("next_steps", []),
            branches=root_data.get("branches", []),
            next_node_id=root_data.get("next_node", ""),
            legal_refs=root_data.get("legal_refs", []),
        )
        return DecisionTree(
            tree_id=data["tree_id"],
            title=data["title"],
            law=data["law"],
            article=data["article"],
            description=data["description"],
            root=root,
            nodes=nodes,
            tags=data.get("tags", []),
        )

    # ── Búsqueda de árbol por query ─────────────────────────────

    def _rank_trees_keyword(self, query: str) -> list[tuple[str, int]]:
        """Retorna los tree_ids candidatos ordenados por score de palabras clave."""
        q = self._normalize(query)
        scores: dict[str, int] = {}
        for keyword, tree_id in self._index.items():
            if keyword in q:
                scores[tree_id] = scores.get(tree_id, 0) + 1
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    async def find_tree(
        self,
        query: str,
        llm_client=None,
    ) -> DecisionTree | None:
        """
        Busca el árbol más relevante para una query.
        Primero filtra por keywords, luego usa LLM para desempatar.
        """
        ranked = self._rank_trees_keyword(query)
        if not ranked:
            return None

        # Si hay un claro ganador (score >= 2 y diferencia >= 2), úsalo directo
        if len(ranked) == 1 or (ranked[0][1] >= 2 and ranked[0][1] - ranked[1][1] >= 2):
            return self._trees.get(ranked[0][0])

        # Si hay empate o score bajo, usar LLM para clasificar
        if llm_client is None:
            return self._trees.get(ranked[0][0])

        top_ids = [tid for tid, _ in ranked[:3]]
        options = "\n".join(
            f"{i+1}. {self._trees[tid].tree_id}: {self._trees[tid].title}"
            for i, tid in enumerate(top_ids)
        )
        prompt = (
            f"El usuario pregunta: \"{query}\"\n\n"
            f"¿Cuál de estos temas del Código Tributario es el más relevante?\n"
            f"{options}\n\n"
            f"Responde ÚNICAMENTE con el número (1, 2 o 3). Si ninguno aplica, responde 0."
        )
        try:
            resp = await llm_client.chat_completion(
                messages=[
                    {"role": "system", "content": "Eres un clasificador de temas tributarios. Respondes solo un número."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=10,
            )
            choice = int(resp.strip().split()[0])
            if 1 <= choice <= len(top_ids):
                return self._trees.get(top_ids[choice - 1])
        except Exception:
            pass
        return self._trees.get(ranked[0][0])

    def list_trees(self) -> list[tuple[str, str]]:
        """Devuelve (tree_id, title) de todos los árboles cargados."""
        return [(t.tree_id, t.title) for t in self._trees.values()]

    # ── Recorrido del árbol ─────────────────────────────────────

    def walk_tree(
        self,
        tree: DecisionTree,
        facts: dict[str, Any],
    ) -> tuple[DecisionNode, list[DecisionNode]]:
        """
        Recorre el árbol usando los hechos proporcionados.

        Args:
            facts: dict con respuestas del usuario, ej:
                   {"citacion": True, "escrita": True, "director": True}

        Returns:
            (nodo_resultado, camino_recorrido)
        """
        path: list[DecisionNode] = []
        current = tree.root
        visited: set[str] = set()

        while current.id not in visited:
            visited.add(current.id)
            path.append(current)

            if current.type == "result":
                break

            if current.type == "info":
                if current.next_node_id and current.next_node_id in tree.nodes:
                    current = tree.nodes[current.next_node_id]
                    continue
                break

            if current.type == "decision":
                next_node_id = self._resolve_branches(current, facts)
                if next_node_id and next_node_id in tree.nodes:
                    current = tree.nodes[next_node_id]
                    continue
                # Si no hay match, nos quedamos en el último nodo conocido
                break

        return current, path

    def _resolve_branches(self, node: DecisionNode, facts: dict[str, Any]) -> str:
        """Determina qué rama seguir basado en los hechos."""
        for branch in node.branches:
            condition = branch.get("condition", "")
            # Si la condición es un booleano explícito en facts
            if condition in facts and facts[condition] is True:
                return branch.get("next_node", "")
            # Si la condición es una string que matcha un valor
            for key, val in facts.items():
                if str(val).lower() in condition.lower():
                    return branch.get("next_node", "")
        return ""

    # ── Renderizado ─────────────────────────────────────────────

    def render_result(
        self,
        tree: DecisionTree,
        result_node: DecisionNode,
        path: list[DecisionNode],
        include_diagram: bool = True,
    ) -> str:
        """
        Genera la respuesta final en texto plano (con opción de diagrama).
        """
        lines: list[str] = []

        # Título del tema
        lines.append(f"📋 {tree.title}")
        lines.append(f"📖 {tree.law} | {tree.article}")
        lines.append("")

        # Respuesta
        lines.append(result_node.summary)
        if result_node.details:
            lines.append("")
            lines.append(result_node.details)

        # Advertencias
        if result_node.warnings:
            lines.append("")
            lines.append("⚠️ ADVERTENCIAS:")
            for w in result_node.warnings:
                lines.append(f"  • {w}")

        # Próximos pasos
        if result_node.next_steps:
            lines.append("")
            lines.append("➡️ PRÓXIMOS PASOS:")
            for s in result_node.next_steps:
                lines.append(f"  • {s}")

        # Referencias legales
        refs = result_node.legal_refs or ([result_node.legal_ref] if result_node.legal_ref else [])
        if refs:
            lines.append("")
            lines.append("📚 FUNDAMENTO LEGAL:")
            for r in refs:
                lines.append(f"  • {r}")

        # Diagrama del camino recorrido
        if include_diagram and path:
            lines.append("")
            lines.append(self._render_diagram(path, result_node))

        return "\n".join(lines)

    def _render_diagram(self, path: list[DecisionNode], result: DecisionNode) -> str:
        """Genera un diagrama ASCII del camino recorrido."""
        lines: list[str] = ["🌳 CAMINO DE DECISIÓN RECORRIDO:", ""]
        for i, node in enumerate(path):
            indent = "  " * i
            if node.type == "decision":
                lines.append(f"{indent}├─❓ {node.question}")
            elif node.type == "info":
                lines.append(f"{indent}├─ℹ️  {node.summary[:60]}...")
        # Resultado final
        indent = "  " * len(path)
        lines.append(f"{indent}└─✅ RESULTADO: {result.summary[:70]}")
        return "\n".join(lines)

    def render_interactive(self, node: DecisionNode) -> str:
        """Renderiza un nodo de decisión para que el usuario elija."""
        lines: list[str] = []
        lines.append(f"❓ {node.question}")
        if node.help_text:
            lines.append(f"💡 {node.help_text}")
        if node.legal_ref:
            lines.append(f"📖 Fundamento: {node.legal_ref}")
        lines.append("")
        lines.append("Opciones:")
        for i, branch in enumerate(node.branches, 1):
            lines.append(f"  {i}. {branch['condition']}")
        return "\n".join(lines)


    # ── Query Interpreter (LLM) ─────────────────────────────────

    async def interpret_query(
        self,
        query: str,
        tree: DecisionTree,
        llm_client=None,
    ) -> dict[str, Any]:
        """
        Usa un LLM para extraer hechos (facts) de la pregunta del usuario
        que permitan navegar el árbol de decisión.
        """
        if llm_client is None:
            return {}

        # Recolectar todas las condiciones posibles del árbol
        conditions: set[str] = set()
        # Incluir nodo raíz
        if tree.root.type == "decision":
            for b in tree.root.branches:
                conditions.add(b["condition"])
        # Incluir nodos secundarios
        for nid, node in tree.nodes.items():
            if node.type == "decision":
                for b in node.branches:
                    conditions.add(b["condition"])

        conds_list = ", ".join(sorted(conditions)[:30])

        prompt = (
            f"Extrae hechos booleanos del texto del usuario para un árbol de decisión sobre: {tree.title}.\n"
            f"Condiciones posibles: {conds_list}\n\n"
            f'Texto: "{query}"\n\n'
            "Responde SOLO un JSON con las condiciones que aplican como true. "
            "Ejemplo: {\"sii_citado\": true, \"escrita\": true}\n"
            "Si no hay info suficiente, responde {}."
        )

        try:
            response = await llm_client.chat_completion(
                messages=[
                    {"role": "system", "content": "Eres un extractor JSON. Solo respondes JSON válido."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=300,
            )
            raw = response.strip()
            # Limpiar fences markdown
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("\n", 1)[0]
            if raw.startswith("json"):
                raw = raw[4:].strip()
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            return {}
        except Exception as e:
            console.print(f"[yellow][INTERPRETER] Falló: {e} | raw: {response[:200] if 'response' in dir() else 'N/A'}[/yellow]")
            return {}

    async def navigate_tree(
        self,
        query: str,
        llm_client=None,
    ) -> tuple[DecisionTree | None, DecisionNode | None, list[DecisionNode], dict[str, Any]]:
        """
        Encuentra el árbol y lo navega usando el Query Interpreter.

        Returns:
            (tree, result_node, path, facts)
            tree puede ser None si no hay árbol relevante.
            result_node puede ser None si faltan datos para llegar a una hoja.
        """
        tree = await self.find_tree(query, llm_client)
        if not tree:
            return None, None, [], {}
        facts = await self.interpret_query(query, tree, llm_client)
        result, path = self.walk_tree(tree, facts)
        return tree, result, path, facts

    def continue_tree(
        self,
        tree: DecisionTree,
        current_node_id: str,
        facts: dict[str, Any],
    ) -> tuple[DecisionNode, list[DecisionNode], bool]:
        """
        Continúa el recorrido desde los facts acumulados.
        
        Returns:
            (result_node, path, advanced)
            advanced: True si se avanzó al menos un nodo desde current_node_id
        """
        result, path = self.walk_tree(tree, facts)
        
        # Verificar si avanzamos desde el nodo anterior
        old_idx = -1
        for i, node in enumerate(path):
            if node.id == current_node_id:
                old_idx = i
                break
        
        advanced = old_idx >= 0 and len(path) > old_idx + 1
        return result, path, advanced


# Instancia global
engine = DecisionEngine()
