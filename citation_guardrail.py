"""
Guardrails de citas legales — verifica que el LLM no alucine normas.

Flujo:
1. Extrae citas de la respuesta (Artículo X, Art. X, Ley Y, etc.)
2. Verifica que existan en el contexto proporcionado al LLM
3. Si hay citas sospechosas, agrega una advertencia o corrige
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rich.console import Console

console = Console()


@dataclass
class CitationCheck:
    """Resultado de verificación de una cita."""

    text: str
    found_in_context: bool
    suggested_correction: str | None = None


class CitationGuardrail:
    """Verifica que las citas legales en una respuesta existan en las fuentes."""

    # Conceptos inventados comunes que NO existen en la legislación chilena vigente
    _HALLUCINATION_PATTERNS: list[tuple[re.Pattern, str]] = [
        (re.compile(r"exención.*vivienda habitual", re.IGNORECASE), "No existe 'exención por vivienda habitual' en la LIR chilena"),
        (re.compile(r"vivienda habitual.*exenta", re.IGNORECASE), "No existe exención por vivienda habitual en Chile"),
        (re.compile(r"periodo mínimo de posesión.*inmueble", re.IGNORECASE), "No existe periodo mínimo de posesión para inmuebles en la LIR"),
        (re.compile(r"plazo mínimo.*posesión.*bien raíz", re.IGNORECASE), "No existe plazo mínimo de posesión para bienes raíces"),
        (re.compile(r"reinversión de utilidades.*vigente", re.IGNORECASE), "La reinversión de utilidades (Art. 14 A N° 1 letra c) fue derogada en 2014"),
        (re.compile(r"retiro para reinvertir.*aplica", re.IGNORECASE), "El retiro para reinvertir fue derogado en 2014"),
        (re.compile(r"Art\.?\s*14\s*[A-Za-z]*\s*N[°o]?\s*1\s*letra\s*c.*rebaja", re.IGNORECASE), "Art. 14 A N° 1 letra c fue derogado; el 50% está en Art. 14 E"),
        (re.compile(r"Art\.?\s*17\s*N[°o]?\s*11.*inmueble", re.IGNORECASE), "El Art. 17 N° 11 no existe; la exención de 8.000 UF está en Art. 17 N° 8"),
    ]

    # Patrones para detectar citas legales en español
    _ARTICLE_PATTERNS = [
        re.compile(r"Art[íi]culo\s+(\d+[°\w]*)\s+(?:de\s+la\s+)?(?:Ley\s+(?:sobre\s+)?(?:Impuesto\s+a\s+la\s+)?(?:C[oó]digo\s+)?([\w\s]+))", re.IGNORECASE),
        re.compile(r"Art\.?\s+(\d+[°\w]*)\s+(?:de\s+la\s+)?(?:Ley\s+(?:sobre\s+)?(?:Impuesto\s+a\s+la\s+)?(?:C[oó]digo\s+)?([\w\s]+))", re.IGNORECASE),
        re.compile(r"Art[íi]culo\s+(\d+[°\w]*)\s+(?:del\s+)?(?:C[oó]digo\s+Tributario|C[oó]digo\s+del\s+Trabajo)", re.IGNORECASE),
        re.compile(r"Art\.?\s+(\d+[°\w]*)\s+(?:del\s+)?(?:C[oó]digo\s+Tributario|C[oó]digo\s+del\s+Trabajo)", re.IGNORECASE),
        re.compile(r"Art[íi]culo\s+(\d+[°\w]*)\s+de\s+la\s+Ley\s+(?:sobre\s+)?(?:Impuesto\s+a\s+la\s+)?Renta", re.IGNORECASE),
        re.compile(r"Art\.?\s+(\d+[°\w]*)\s+de\s+la\s+Ley\s+(?:sobre\s+)?(?:Impuesto\s+a\s+la\s+)?Renta", re.IGNORECASE),
        re.compile(r"Art[íi]culo\s+(\d+[°\w]*)\s+de\s+la\s+Ley\s+del\s+IVA", re.IGNORECASE),
        re.compile(r"Art\.?\s+(\d+[°\w]*)\s+de\s+la\s+Ley\s+del\s+IVA", re.IGNORECASE),
        re.compile(r"Art[íi]culo\s+(\d+[°\w]*)\s+del\s+DL[-\s]?(824|825|830)", re.IGNORECASE),
        re.compile(r"Art\.?\s+(\d+[°\w]*)\s+del\s+DL[-\s]?(824|825|830)", re.IGNORECASE),
        re.compile(r"DL[-\s]?(824|825|830)", re.IGNORECASE),
    ]

    # Mapeo de nombres de ley a law_tag
    _LAW_NAME_MAP: dict[str, str] = {
        "renta": "lir",
        "impuesto a la renta": "lir",
        "ley de renta": "lir",
        "iva": "iva",
        "impuesto al valor agregado": "iva",
        "impuesto a las ventas": "iva",
        "codigo tributario": "codigo_tributario",
        "código tributario": "codigo_tributario",
        "dl 824": "lir",
        "dl-824": "lir",
        "dl 825": "iva",
        "dl-825": "iva",
        "dl 830": "codigo_tributario",
        "dl-830": "codigo_tributario",
    }

    def __init__(self) -> None:
        self._context_articles: set[str] = set()

    def load_context(self, context_text: str) -> None:
        """Extrae todos los números de artículo mencionados en el contexto."""
        self._context_articles = set()
        # Buscar ARTÍCULO: X en las marcas del contexto
        for match in re.finditer(r"ART[ÍI]CULO:\s*(\d+[\w\s]*)", context_text, re.IGNORECASE):
            self._context_articles.add(self._normalize_article(match.group(1)))
        # Buscar UIDs tipo ley_lir_art_21
        for match in re.finditer(r"ley_(lir|iva|codigo_tributario)_art_(\d+[\w_]*)", context_text):
            law = match.group(1)
            art = match.group(2).replace("_", " ")
            self._context_articles.add(f"{law}:{art}")
        # Buscar menciones de artículo en el contenido
        for match in re.finditer(r"[Aa]rt[íi]culo\s+(\d+[°\w]*)", context_text):
            self._context_articles.add(self._normalize_article(match.group(1)))

    @staticmethod
    def _normalize_article(num: str) -> str:
        """Normaliza '21°', '21.', '21' → '21'."""
        return re.sub(r"[°º\.\-]+$", "", num.strip()).lower()

    def _detect_law_tag(self, cita_text: str) -> str | None:
        """Detecta qué ley se cita."""
        cita_lower = cita_text.lower()
        for name, tag in self._LAW_NAME_MAP.items():
            if name in cita_lower:
                return tag
        return None

    def check_response(self, response_text: str) -> list[CitationCheck]:
        """Verifica todas las citas en una respuesta."""
        checks: list[CitationCheck] = []
        found_citations: set[str] = set()

        for pattern in self._ARTICLE_PATTERNS:
            for match in pattern.finditer(response_text):
                cita_text = match.group(0)
                if cita_text in found_citations:
                    continue
                found_citations.add(cita_text)

                # Verificar si existe en el contexto
                article_num = None
                if len(match.groups()) >= 1:
                    article_num = self._normalize_article(match.group(1))

                law_tag = self._detect_law_tag(cita_text)

                found = False
                if law_tag and article_num:
                    composite = f"{law_tag}:{article_num}"
                    if composite in self._context_articles or article_num in self._context_articles:
                        found = True
                elif article_num and article_num in self._context_articles:
                    found = True

                checks.append(CitationCheck(
                    text=cita_text,
                    found_in_context=found,
                ))

        return checks

    def check_hallucinations(self, response_text: str) -> list[str]:
        """Detecta conceptos inventados que no existen en la legislación chilena."""
        found: list[str] = []
        for pattern, message in self._HALLUCINATION_PATTERNS:
            if pattern.search(response_text):
                found.append(message)
        return found

    def annotate_response(self, response_text: str) -> str:
        """Agrega advertencias a la respuesta si hay citas no verificadas o alucinaciones."""
        checks = self.check_response(response_text)
        unverified = [c for c in checks if not c.found_in_context]
        hallucinations = self.check_hallucinations(response_text)

        notes: list[str] = []

        if hallucinations:
            notes.append(
                "\n\n[ALERTA] Se detectaron posibles conceptos no vigentes o inexistentes en la legislación chilena: "
                + "; ".join(hallucinations)
            )

        if unverified:
            note = (
                "\n\n[WARN] No pude confirmar en las fuentes consultadas: "
                + ", ".join(f"'{c.text}'" for c in unverified[:3])
            )
            if len(unverified) > 3:
                note += f" y {len(unverified) - 3} citas más."
            note += " Por favor verifica estas citas directamente en la norma."
            notes.append(note)

        if notes:
            return response_text + "".join(notes)

        return response_text


def guardrail_check(context_text: str, response_text: str) -> str:
    """Función helper: verifica citas y anota la respuesta."""
    guardrail = CitationGuardrail()
    guardrail.load_context(context_text)
    return guardrail.annotate_response(response_text)
