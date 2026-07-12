"""
Parser jerárquico universal para leyes y códigos.

Reemplaza los scripts ad-hoc por artículo (extract_article_14_letters,
extract_article_17_n8 en scripts/extract_clean_articles.py, y los
patrones duplicados en scripts/rechunk_todas_las_leyes.py) con una tabla
de patrones reutilizable por tipo de documento. Una ley nueva se agrega
registrando su patrón en DOCUMENT_PATTERNS, no escribiendo código nuevo.

Circulares y jurisprudencia siguen usando los parsers ya existentes en
ingest.py (JurisprudenciaMDParser, CircularMDParser) porque llegan
pre-estructuradas desde los scrapers; este módulo cubre el caso que
faltaba: texto plano de leyes/códigos sin estructura previa.

Los scripts especiales (extract_article_14_letters, extract_article_17_n8)
quedan como fallback documentado para los casos ya validados a mano;
producían resultados correctos y no hace falta re-derivarlos.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
from extract_clean_articles import chunk_text, clean_text  # noqa: E402

from models import DocumentChunk
from schemas import DocumentPattern, LegalNode

# ------------------------------------------------------------------
# Tabla de patrones por tipo de documento.
# ------------------------------------------------------------------

DOCUMENT_PATTERNS: dict[str, DocumentPattern] = {
    "ley": DocumentPattern(
        doc_type="ley",
        article_patterns=[
            r"ARTICULO\s+(\d+[\w°º]*)\.?\s*-",
            r"Artículo\s+(\d+[\w°º]*)\.?\s*-",
        ],
        numeral_pattern=r"\n\s*(\d+)[°º]\.?[-\s]",
        letra_pattern=r"\n\s*\(?([a-z])\)\s",
        transitorio_marker=r"ART[IÍ]CULOS?\s+TRANSITORIOS?",
    ),
}

# Prefijo de uid por tipo de nodo (matching de la convención ya usada en
# insert_rechunked.py: ley_lir_art_14_e, ley_lir_art_17_n8).
_UID_SEGMENT = {
    "articulo": lambda ident: f"art_{ident}",
    "numeral": lambda ident: f"n{ident}",
    "letra": lambda ident: ident.lower(),
    "inciso": lambda ident: f"inc_{ident}",
}


class LegalParser:
    """Parsea texto legal plano a un AST jerárquico y lo aplana a chunks."""

    def __init__(self, patterns: dict[str, DocumentPattern] | None = None) -> None:
        self._patterns = patterns or DOCUMENT_PATTERNS

    # ── Segmentación ─────────────────────────────────────────────

    def _find_spans(self, text: str, pattern: str) -> list[tuple[str, int, int]]:
        """Encuentra ocurrencias de `pattern` y devuelve (identificador, start, end)."""
        matches = [(m.group(1), m.start()) for m in re.finditer(pattern, text)]
        matches.sort(key=lambda x: x[1])
        spans: list[tuple[str, int, int]] = []
        for i, (ident, start) in enumerate(matches):
            end = matches[i + 1][1] if i + 1 < len(matches) else len(text)
            spans.append((ident, start, end))
        return spans

    def _find_articles(self, text: str, pattern: DocumentPattern) -> list[tuple[str, int, int]]:
        raw: list[tuple[str, int]] = []
        for pat in pattern.article_patterns:
            for m in re.finditer(pat, text):
                num = m.group(1).replace("°", "").replace("º", "")
                raw.append((num, m.start()))
        raw.sort(key=lambda x: x[1])

        deduped: list[tuple[str, int]] = []
        for ident, start in raw:
            if deduped and start - deduped[-1][1] < 10:
                continue
            deduped.append((ident, start))

        spans: list[tuple[str, int, int]] = []
        for i, (ident, start) in enumerate(deduped):
            end = deduped[i + 1][1] if i + 1 < len(deduped) else len(text)
            spans.append((ident, start, end))
        return spans

    def _split_children(self, text: str, pattern: DocumentPattern) -> list[LegalNode]:
        """Intenta partir `text` en numerales; si no hay, intenta letras."""
        if pattern.numeral_pattern:
            numeral_spans = self._find_spans(text, pattern.numeral_pattern)
            if len(numeral_spans) >= 2:
                nodes = []
                for ident, start, end in numeral_spans:
                    sub_text = clean_text(text[start:end])
                    if not sub_text:
                        continue
                    grandchildren = []
                    if pattern.letra_pattern:
                        letra_spans = self._find_spans(sub_text, pattern.letra_pattern)
                        if len(letra_spans) >= 2:
                            grandchildren = [
                                LegalNode(node_type="letra", identifier=lid, text=clean_text(sub_text[ls:le]))
                                for lid, ls, le in letra_spans
                                if clean_text(sub_text[ls:le])
                            ]
                    nodes.append(LegalNode(node_type="numeral", identifier=ident, text=sub_text, children=grandchildren))
                return nodes

        if pattern.letra_pattern:
            letra_spans = self._find_spans(text, pattern.letra_pattern)
            if len(letra_spans) >= 2:
                return [
                    LegalNode(node_type="letra", identifier=ident, text=clean_text(text[start:end]))
                    for ident, start, end in letra_spans
                    if clean_text(text[start:end])
                ]

        return []

    def parse_articles(self, text: str, doc_type: str = "ley") -> list[LegalNode]:
        """Segmenta un texto legal completo en nodos de artículo (con hijos si existen).

        Si el patrón define `transitorio_marker`, el texto se parte en cuerpo
        principal + artículos transitorios (que renumeran desde 1 en casi
        todas las leyes chilenas) para no generar identificadores duplicados.
        """
        pattern = self._patterns.get(doc_type)
        if pattern is None:
            raise ValueError(f"No hay patrón registrado para doc_type={doc_type!r}")

        main_text = text
        transitorio_text = ""
        if pattern.transitorio_marker:
            m = re.search(pattern.transitorio_marker, text)
            if m:
                main_text = text[: m.start()]
                transitorio_text = text[m.start() :]

        nodes = self._parse_body(main_text, pattern, prefix="")
        if transitorio_text:
            nodes.extend(self._parse_body(transitorio_text, pattern, prefix="trans_"))
        return nodes

    def _parse_body(self, text: str, pattern: DocumentPattern, *, prefix: str) -> list[LegalNode]:
        nodes: list[LegalNode] = []
        for ident, start, end in self._find_articles(text, pattern):
            cleaned = clean_text(text[start:end])
            if not cleaned:
                continue
            children = self._split_children(cleaned, pattern)
            nodes.append(LegalNode(node_type="articulo", identifier=f"{prefix}{ident}", text=cleaned, children=children))
        return nodes

    # ── Aplanado a DocumentChunk ────────────────────────────────

    def to_document_chunks(
        self,
        nodes: list[LegalNode],
        *,
        law_tag: str,
        source_path: str,
        filename: str,
        source_type: str = "ley",
        max_chars: int = 3500,
    ) -> list[DocumentChunk]:
        """Aplana el AST a DocumentChunk, poblando hierarchy_path/section_level_name.

        Deriva `chunk_uid` de la jerarquía (ley/artículo/numeral/letra). Rara
        vez dos ramas distintas del texto fuente producen el mismo número de
        artículo por fuera de la estructura modelada (p.ej. el artículo 1° del
        decreto promulgatorio vs. el artículo 1° del texto anexado) — se
        desambigua con un sufijo `_dupN` para no violar el unique constraint
        de `document_chunks.chunk_uid` en Supabase.
        """
        chunks: list[DocumentChunk] = []
        for node in nodes:
            chunks.extend(
                self._flatten(
                    node,
                    ancestors=[],
                    law_tag=law_tag,
                    source_path=source_path,
                    filename=filename,
                    source_type=source_type,
                    max_chars=max_chars,
                )
            )
        return self._dedupe_uids(chunks)

    @staticmethod
    def _dedupe_uids(chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        seen: dict[str, int] = {}
        for chunk in chunks:
            uid = chunk.chunk_uid
            if uid not in seen:
                seen[uid] = 1
                continue
            seen[uid] += 1
            chunk.chunk_uid = f"{uid}_dup{seen[uid]}"
        return chunks

    def _flatten(
        self,
        node: LegalNode,
        ancestors: list[LegalNode],
        *,
        law_tag: str,
        source_path: str,
        filename: str,
        source_type: str,
        max_chars: int,
    ) -> list[DocumentChunk]:
        chain = ancestors + [node]

        if node.children:
            result: list[DocumentChunk] = []
            for child in node.children:
                result.extend(
                    self._flatten(
                        child,
                        chain,
                        law_tag=law_tag,
                        source_path=source_path,
                        filename=filename,
                        source_type=source_type,
                        max_chars=max_chars,
                    )
                )
            return result

        uid = "_".join([f"ley_{law_tag}"] + [_UID_SEGMENT[n.node_type](n.identifier) for n in chain])
        hierarchy_path = "/".join(f"{n.node_type}_{n.identifier}" for n in chain)
        section_name = " ".join(f"{n.node_type.upper()} {n.identifier}" for n in chain) + f" {law_tag.upper()}"

        sub_chunks = chunk_text(node.text, max_chars=max_chars)
        result = []
        for idx, sub in enumerate(sub_chunks):
            this_uid = uid if idx == 0 else f"{uid}_{idx}"
            result.append(
                DocumentChunk(
                    chunk_uid=this_uid,
                    source_path=source_path,
                    filename=filename,
                    source_type=source_type,
                    content=sub,
                    content_hash=hashlib.sha256(sub.encode()).hexdigest()[:32],
                    law_tag=law_tag,
                    hierarchy_path=hierarchy_path,
                    section_level_name=section_name,
                    chunk_index=idx,
                    total_chunks=len(sub_chunks),
                )
            )
        return result


parser = LegalParser()
