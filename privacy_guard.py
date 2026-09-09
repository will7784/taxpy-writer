"""Minimiza datos personales antes de buscar o consultar modelos externos.

La correspondencia existe sólo mientras se procesa una investigación. No se
persiste junto con la biblioteca ni se exporta al vault: el informe usa los
marcadores para que el asesor pueda reponer los datos desde el expediente.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionResult:
    text: str
    replacements: int


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("[RUT OMITIDO]", re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b")),
    ("[EMAIL OMITIDO]", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("[TELÉFONO OMITIDO]", re.compile(r"(?<!\d)(?:\+?56\s*)?(?:9\s*)?\d{4}\s*\d{4}(?!\d)")),
    ("[CUENTA OMITIDA]", re.compile(r"\b(?:cuenta|cta\.?|iban)\s*(?:n[°ºo.]?\s*)?[:#-]?\s*[0-9][0-9 .-]{5,}[0-9]\b", re.I)),
    ("[DIRECCIÓN OMITIDA]", re.compile(
        r"\b(?:calle|av(?:enida)?\.?|pasaje|camino)\s+[A-Za-zÁÉÍÓÚÑáéíóúñ.' -]{2,80}\s+\d{1,6}\b",
        re.I,
    )),
)

_POSSIBLE_NAME = re.compile(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})+\b")
_PUBLIC_LEGAL_NAMES = {
    "biblioteca del congreso", "codigo civil", "codigo penal", "codigo tributario",
    "codigo de comercio", "codigo procesal", "codigo de procedimiento", "corte suprema",
    "poder judicial", "diario oficial", "ley chile", "tribunal tributario",
}


def _redact_possible_name(match: re.Match[str]) -> str:
    normalized = "".join(
        ch for ch in unicodedata.normalize("NFKD", match.group(0).lower())
        if not unicodedata.combining(ch) and (ch.isalpha() or ch.isspace())
    )
    if any(public in normalized for public in _PUBLIC_LEGAL_NAMES):
        return match.group(0)
    return "[PERSONA O ENTIDAD OMITIDA]"


def redact_for_external(text: str) -> RedactionResult:
    """Devuelve un texto apto para búsqueda y modelos remotos.

    Seudonimiza secuencias de nombres o entidades en mayúscula inicial y
    conserva una lista pequeña de nombres jurídicos públicos para no degradar
    la consulta. El expediente local conserva los antecedentes originales.
    """
    redacted = text
    count = 0
    for replacement, pattern in _PATTERNS:
        redacted, changed = pattern.subn(replacement, redacted)
        count += changed
    redacted, changed = _POSSIBLE_NAME.subn(_redact_possible_name, redacted)
    # El contador sólo crece cuando la sustitución fue efectiva; las fuentes y
    # cuerpos legales públicos que coinciden con el patrón se mantienen.
    count += sum(1 for match in _POSSIBLE_NAME.finditer(text) if _redact_possible_name(match) != match.group(0))
    return RedactionResult(text=redacted, replacements=count)
