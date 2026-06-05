"""
Extracción automática de relaciones del grafo usando LLM.

Cada chunk nuevo se analiza con GPT-4o-mini para detectar qué artículos,
leyes, jurisprudencia o conceptos menciona, generando aristas para el grafo.
"""

from __future__ import annotations

import json
import re
from typing import Any

from rich.console import Console

from llm_client import LLMClient
from models import DocumentChunk

console = Console()

# Prompt optimizado para GPT-4o-mini: extrae relaciones legales de un fragmento
_EXTRACTION_PROMPT = """Eres un analista legal experto en derecho tributario chileno.
Tu tarea es leer el siguiente fragmento de una ley, circular, oficio o jurisprudencia,
y extraer TODAS las relaciones legales que encuentres.

Reglas:
1. Identifica qué artículos, leyes, decretos, circulares o fallos menciona el texto.
2. Identifica qué conceptos clave define o regula el texto.
3. Para cada referencia encontrada, crea una relación con el tipo apropiado.

Tipos de relación permitidos:
- "menciona": cuando el texto nombra explícitamente otro artículo o ley.
- "interpreta": cuando el texto explica el sentido de otro artículo.
- "deroga": cuando el texto anula o modifica otro artículo.
- "complementa": cuando el texto agrega reglas a otro artículo sin contradecirlo.
- "beneficia_a": cuando el texto otorga un beneficio o régimen especial a un grupo.
- "regula": cuando el texto establece normas sobre un concepto.
- "relacionado_con": para cualquier otra conexión relevante.

Formato de salida (JSON obligatorio, sin explicaciones):
{
  "relations": [
    {"source": "UID del chunk actual", "target": "artículo/ley mencionado", "type": "menciona"},
    ...
  ]
}

Normas para el campo "target":
- Si menciona un artículo de la Ley de Renta (DL-824), usa: "ley_lir_art_XX"
- Si menciona un artículo del Código Tributario (DL-830), usa: "ley_codigo_tributario_art_XX"
- Si menciona un artículo de la Ley del IVA (DL-825), usa: "ley_iva_art_XX"
- Si menciona una circular u oficio, usa el nombre del archivo.
- Si no estás seguro del UID exacto, usa el formato más cercano posible.
- Si el texto menciona un concepto abstracto (ej: "PRO-PYME", "renta presunta"),
  usa el UID del artículo que regula ese concepto como target.

Fragmento a analizar:
---
{content}
---

Chunk UID: {chunk_uid}
"""


class GraphExtractor:
    """Extrae relaciones legales de chunks usando LLM."""

    def __init__(self) -> None:
        self._llm = LLMClient()

    async def extract_relations(self, chunk: DocumentChunk) -> list[dict]:
        """Extrae relaciones de UN chunk."""
        prompt = _EXTRACTION_PROMPT.replace(
            "{content}", chunk.content[:6000]
        ).replace(
            "{chunk_uid}", chunk.chunk_uid
        )

        try:
            response = await self._llm.chat_completion(
                messages=[
                    {"role": "system", "content": "Eres un analista legal que extrae relaciones en JSON puro."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=1000,
                model="gpt-4o-mini",
            )
        except Exception as e:
            console.print(f"[yellow]WARN LLM fallo en extraccion para {chunk.chunk_uid}: {e}[/yellow]")
            return []

        return self._parse_response(response, chunk.chunk_uid)

    async def extract_relations_batch(self, chunks: list[DocumentChunk]) -> list[dict]:
        """Extrae relaciones de múltiples chunks."""
        all_relations: list[dict] = []
        for chunk in chunks:
            relations = await self.extract_relations(chunk)
            all_relations.extend(relations)
        return all_relations

    @staticmethod
    def _parse_response(text: str, default_source: str) -> list[dict]:
        """Parsea la respuesta JSON del LLM."""
        if not text:
            return []

        # Extraer bloque JSON si está envuelto en markdown
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            text = json_match.group(1)
        else:
            # Buscar el primer objeto JSON
            json_match = re.search(r"(\{.*\})", text, re.DOTALL)
            if json_match:
                text = json_match.group(1)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            console.print(f"[yellow]WARN JSON invalido en respuesta del LLM, intentando limpiar...[/yellow]")
            # Limpiar comillas fancy y caracteres extraños
            cleaned = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                console.print(f"[red]FAIL No se pudo parsear JSON del LLM[/red]")
                return []

        relations: list[dict] = []
        raw_relations = data.get("relations", [])
        if not isinstance(raw_relations, list):
            return []

        for rel in raw_relations:
            if not isinstance(rel, dict):
                continue
            source = rel.get("source", default_source)
            target = rel.get("target", "")
            rel_type = rel.get("type", "relacionado_con")
            if not target or not source:
                continue
            relations.append({
                "source_chunk_uid": source,
                "target_chunk_uid": target,
                "relation_type": rel_type,
                "confidence": 0.8,
                "extracted_by": "llm",
            })

        return relations
