"""
Law Map — mapa jerárquico de una ley

Genera, a partir del texto legal completo, un índice estructurado por
Jerarquías (LIBRO → TÍTULO → PÁRRAFO) que describe qué materias y qué rango
de artículos cubre cada sección.

Objetivo: que el LLM sepa DÓNDE buscar un tema DENTRO de la ley antes de
leerla por completo. Esto reduce la carga en la ventana de contexto y evita
que el modelo "invente" la ubicación de un artículo al no encontrar en qué
parte del cuerpo legal se regula (la alucinación de las "páginas del medio").

No depende de Supabase ni de la base de conocimiento: se reconstruye desde el
.txt de la ley al primer uso y se cachea en memoria por ley. Es 100% local y
replicable (se copia con el vault / una carpeta por profesional).

Uso:
    from context_rag.law_map import law_map
    mapa = law_map.build_for_tag("ct")
    print(mapa.to_text())  # índice legible para el prompt
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rich.console import Console

from context_rag.law_loader import law_loader

console = Console()


# ── Detección de cabeceras estructurales ─────────────────────────────

_STRUCT_HEADER = re.compile(
    r"^(LIBRO\s+\w+|T[ÍI]TULO\s+\w+|P[ÁA]RRAFO\s+\d.*)$",
    re.IGNORECASE,
)
_ARTICLE_HEADER = re.compile(
    r"^Art[íi]culo\s+(\d+)(?:°|º)?[\.\-\s]",
    re.IGNORECASE,
)


def _rank(section_type: str) -> int:
    """Orden de jerarquía: 0=ley, 1=LIBRO, 2=TITULO, 3=PARRAFO."""
    return {"LIBRO": 1, "TITULO": 2, "PÁRRAFO": 3, "PARRAFO": 3}.get(
        section_type.upper(), 4
    )


@dataclass
class Section:
    """Una sección estructural (LIBRO/TÍTULO/PÁRRAFO) de la ley."""

    kind: str  # "LIBRO" | "TITULO" | "PARRAFO"
    title: str  # nombre de la sección (ej. "NORMAS GENERALES")
    raw_header: str = ""
    articles: list[str] = field(default_factory=list)  # números de artículo
    children: list["Section"] = field(default_factory=list)  # subsecciones

    @property
    def article_range(self) -> str:
        if not self.articles:
            return ""
        nums = sorted(self.articles, key=lambda n: int(re.sub(r"\D", "", n)) if re.sub(r"\D", "", n) else 0)
        return f"{nums[0]}-{nums[-1]}"


@dataclass
class LawMap:
    """Mapa jerárquico completo de una ley."""

    law_tag: str
    law_name: str
    short_name: str
    sections: list[Section] = field(default_factory=list)  # top-level (LIBRO/TITULO)

    def to_text(self, max_depth: int = 3) -> str:
        """Serializa el mapa a texto legible, listo para inyectar en el prompt."""
        lines: list[str] = []
        lines.append(f"──── ÍNDICE ESTRUCTURAL: {self.law_name} ({self.short_name}) ────")
        lines.append("Estas son las secciones de la ley: LIBRO / TITULO / PARRAFO.")
        lines.append("Usa este índice para ubicar el tema ANTES de leer el artículo.")
        lines.append(f"Artículos entre paréntesis = rango dentro de la sección.\n")

        def _emit(secs: list[Section], depth: int) -> None:
            for s in secs:
                indent = "  " * depth
                rng = f" [Arts. {s.article_range}]" if s.article_range else ""
                lines.append(f"{indent}• {s.kind.title()}: {s.title}{rng}")
                if depth < max_depth:
                    _emit(s.children, depth + 1)

        _emit(self.sections, 0)
        lines.append("")
        return "\n".join(lines)

    def find_section(self, keywords: str) -> str:
        """Búsqueda simple: devuelve la ruta de secciones que matchean keywords."""
        q = keywords.lower()
        hits: list[str] = []

        def _walk(s: Section, ancestors: list[str]) -> None:
            path = ancestors + [s.title]
            if any(k in s.title.lower() for k in q.split() if len(k) > 3):
                hits.append(f"{' > '.join(path)} [Arts. {s.article_range}]")
            for c in s.children:
                _walk(c, path)

        for s in self.sections:
            _walk(s, [])
        return "\n".join(hits[:5])


# ── Construcción desde el texto ──────────────────────────────────────

def _build_map(law_tag: str, law_name: str, short_name: str, text: str) -> LawMap:
    lines = text.split("\n")

    # Una sola raíz virtual que acumula todo (por si la ley no arranca con LIBRO)
    roots: list[Section] = []
    stack: list[tuple[int, Section]] = []  # (depth, section) donde depth es rank del nodo hijo

    def _push(section: Section) -> None:
        # Colgar en el ancestro inmediato de rank menor
        while stack and stack[-1][0] >= _rank(section.kind):
            stack.pop()
        if stack:
            stack[-1][1].children.append(section)
        else:
            roots.append(section)
        stack.append((_rank(section.kind), section))

    current: Section | None = None

    for raw in lines:
        s = raw.strip()
        if not s:
            continue

        m_art = _ARTICLE_HEADER.match(s)
        if m_art:
            art_num = m_art.group(1)
            if current is not None:
                current.articles.append(art_num)
            continue

        m_head = _STRUCT_HEADER.match(s)
        if m_head:
            # El título descriptivo suele venir en la línea siguiente
            title = _next_descriptive_line(raw, lines)
            kind = m_head.group(1).split()[0]  # LIBRO / TITULO / PARRAFO
            sec = Section(kind=kind, title=title, raw_header=s)
            current = sec
            _push(sec)

    return LawMap(law_tag=law_tag, law_name=law_name, short_name=short_name, sections=roots)


def _next_descriptive_line(header_line: str, lines: list[str]) -> str:
    """Devuelve la línea de título descriptivo que sigue a una cabecera."""
    idx = None
    for i, l in enumerate(lines):
        if l is header_line:
            idx = i
            break
    if idx is None:
        return header_line
    for l in lines[idx + 1: idx + 3]:
        cand = l.strip()
        if cand and not _STRUCT_HEADER.match(cand) and not _ARTICLE_HEADER.match(cand):
            return cand[:80]
    return header_line


# ── API ──────────────────────────────────────────────────────────────

class LawMapBuilder:
    """Cachea mapas por ley y los construye perezosamente."""

    def __init__(self) -> None:
        self._cache: dict[str, LawMap] = {}

    def build_for_tag(self, tag: str) -> LawMap | None:
        if tag in self._cache:
            return self._cache[tag]
        law = law_loader.get(tag)
        if not law:
            return None
        try:
            mapa = _build_map(tag, law.name, law.short_name, law.text)
        except Exception as e:
            console.print(f"[yellow][LAW_MAP] Fallo construyendo {tag}: {e}[/yellow]")
            mapa = LawMap(law_tag=tag, law_name=law.name, short_name=law.short_name)
        self._cache[tag] = mapa
        return mapa

    def to_text_for(self, tags: list[str]) -> str:
        """Concatena los mapas de varias leyes en un único bloque para el prompt."""
        parts: list[str] = []
        for tag in tags:
            mapa = self.build_for_tag(tag)
            if mapa and mapa.sections:
                parts.append(mapa.to_text())
        return "\n".join(parts)


# Singleton
law_map = LawMapBuilder()
