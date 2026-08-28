"""
Article Index — indice maestro articulo <-> documentos.

Escanea los .md del vault de Obsidian (jurisprudencia, notas, analisis) y de
los directorios de jurisprudencia scrapeada (documents/jurisprudencia_sii*),
extrae las citas a articulos (Art. X + cuerpo legal) y construye un indice
invertido persistido en knowledge/article_index.json.

Es la pieza que permite responder "con todo": ley (context_rag) +
jurisprudencia + notas propias del usuario, de una sola vez.

Uso:
    from article_index import article_index, extract_article_refs

    refs = extract_article_refs("venta de inmueble art 17 n 8 LIR")
    docs = article_index.search("prescripcion art 200 codigo tributario")
    for d in docs:
        print(d.path, d.title, d.refs)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import config

INDEX_FILE = config.KNOWLEDGE_DIR / "article_index.json"

# Directorios escaneados (ademas del vault completo)
EXTRA_DIRS = [
    config.DOCUMENTS_DIR / "jurisprudencia_sii",
    config.DOCUMENTS_DIR / "jurisprudencia_sii_circulares",
]

# Subcarpetas del vault que no se indexan
SKIP_DIRS = {"Templates", ".obsidian", ".trash"}

# ---------------------------------------------------------------------------
# Deteccion de cuerpo legal
# ---------------------------------------------------------------------------

_LAW_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"dl[-\s]?824|ley (?:sobre )?impuesto a la renta|ley de renta|\bLIR\b", re.IGNORECASE), "lir"),
    (re.compile(r"dl[-\s]?825|ley (?:del|de) (?:iva|impuesto al valor agregado)|ventas y servicios", re.IGNORECASE), "iva"),
    (re.compile(r"dl[-\s]?830|c[oó]digo tributario|\bCT\b", re.IGNORECASE), "ct"),
]

_ART_PATTERN = re.compile(
    r"art(?:[íi]culo|\.)?\s*(\d+)\s*(bis|ter|qu[aá]ter|quinquies|sexies)?",
    re.IGNORECASE,
)

# Cita explicita con ley en la misma frase: "Art. 21 del DL-824", etc.
_ART_WITH_LAW_PATTERN = re.compile(
    r"art(?:[íi]culo|\.)?\s*(\d+)\s*(?:bis|ter|qu[aá]ter|quinquies|sexies)?"
    r"[^.\n]{0,80}?"
    r"(dl[-\s]?824|dl[-\s]?825|dl[-\s]?830|c[oó]digo tributario|"
    r"ley (?:sobre )?impuesto a la renta|ley de renta|ley (?:del|de) (?:iva|impuesto al valor agregado))",
    re.IGNORECASE,
)

_DL_TO_TAG = {"824": "lir", "825": "iva", "830": "ct"}


def _law_text_to_tag(text: str) -> str | None:
    """Mapea un fragmento de texto ('dl-824', 'codigo tributario'...) a tag."""
    t = text.lower().replace(" ", "")
    m = re.search(r"(\d{3})", t)
    if m and m.group(1) in _DL_TO_TAG:
        return _DL_TO_TAG[m.group(1)]
    if "tributario" in t:
        return "ct"
    if "renta" in t:
        return "lir"
    if "iva" in t or "valoragregado" in t:
        return "iva"
    return None


def extract_article_refs(text: str) -> list[tuple[str, str]]:
    """Extrae referencias (law_tag, numero_articulo) de un texto.

    Estrategia:
      1. Citas explicitas con ley en la misma frase ("Art. 21 del DL-824").
      2. Articulos sueltos: se atribuyen a los cuerpos legales detectados
         en el documento completo (si no hay ninguno, quedan sin ley y solo
         sirven para busqueda por keyword).
    """
    refs: set[tuple[str, str]] = set()

    # 1. Citas explicitas con ley
    for m in _ART_WITH_LAW_PATTERN.finditer(text):
        art = m.group(1)
        tag = _law_text_to_tag(m.group(2))
        if tag:
            refs.add((tag, art))

    # 2. Articulos sueltos atribuidos a las leyes del documento
    doc_laws = {tag for pattern, tag in _LAW_PATTERNS if pattern.search(text)}
    if doc_laws:
        for m in _ART_PATTERN.finditer(text):
            art = m.group(1)
            for tag in doc_laws:
                refs.add((tag, art))

    return sorted(refs)


def extract_article_numbers(text: str) -> list[str]:
    """Numeros de articulo mencionados, sin importar la ley.

    Util para queries de usuario tipo 'me cito el sii, articulo 63' donde no
    se menciona el cuerpo legal.
    """
    return sorted({m.group(1) for m in _ART_PATTERN.finditer(text)})


# ---------------------------------------------------------------------------
# Documentos indexados
# ---------------------------------------------------------------------------


@dataclass
class VaultDoc:
    path: str
    title: str
    tipo: str = ""
    cliente: str = ""
    refs: list[tuple[str, str]] = field(default_factory=list)
    mtime: float = 0.0
    aprobada: bool = False
    _text: str = field(default="", repr=False)

    @property
    def snippet(self) -> str:
        body = re.sub(r"^---.*?---\s*", "", self._text, flags=re.DOTALL)
        body = re.sub(r"\s+", " ", body).strip()
        return body[:1500]

    def load_text(self) -> str:
        if not self._text:
            try:
                self._text = Path(self.path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._text = ""
        return self._text

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "tipo": self.tipo,
            "cliente": self.cliente,
            "refs": [list(r) for r in self.refs],
            "mtime": self.mtime,
            "aprobada": self.aprobada,
        }


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extrae campos simples del frontmatter YAML sin parsear todo el archivo."""
    meta: dict[str, str] = {}
    m = re.match(r"^---\s*\n(.*?)\n---", text, flags=re.DOTALL)
    if not m:
        return meta
    for line in m.group(1).splitlines():
        kv = re.match(r"(\w+):\s*\"?([^\"\n]*?)\"?\s*$", line)
        if kv:
            meta[kv.group(1)] = kv.group(2).strip()
    return meta


def _parse_bool(value: str | None) -> bool:
    """Interpreta 'true'/'True'/'1' como True (frontmatter YAML sin parsear)."""
    if value is None:
        return False
    return value.strip().lower() in ("true", "1", "yes", "si", "sí")


# ---------------------------------------------------------------------------
# Indice
# ---------------------------------------------------------------------------


def _ensure_frontmatter_aprobada(match_text: str) -> str:
    """Reconstruye el bloque de frontmatter con 'aprobada: true' y tag 'aprobada'."""
    head = match_text
    head = re.sub(r"^\s*aprobada:\s*.*$", "aprobada: true", head, flags=re.MULTILINE)
    if not re.search(r"^aprobada:\s*true\s*$", head, flags=re.MULTILINE):
        head = head.rstrip() + "\naprobada: true"
    # Asegurar tag aprobada
    tag_m = re.search(r"^tags:\s*\[(.*)\]", head, flags=re.MULTILINE)
    if tag_m:
        tags = [t.strip() for t in tag_m.group(1).split(",") if t.strip()]
        if "aprobada" not in tags:
            tags.append("aprobada")
        head = head[:tag_m.start()] + f"tags: [{', '.join(tags)}]" + head[tag_m.end():]
    elif not re.search(r"^tags:", head, flags=re.MULTILINE):
        head = head.rstrip() + "\ntags: [aprobada]"
    return head


class ArticleIndex:
    """Indice invertido (ley, articulo) -> documentos, persistido en JSON."""

    def __init__(self) -> None:
        self._docs: dict[str, VaultDoc] = {}  # path -> doc
        self._by_ref: dict[str, list[str]] = {}  # "lir:21" -> [paths]
        self._loaded = False

    # ── Persistencia ─────────────────────────────────────────────

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not INDEX_FILE.exists():
            return
        try:
            raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            for path, entry in raw.get("files", {}).items():
                doc = VaultDoc(
                    path=path,
                    title=entry.get("title", ""),
                    tipo=entry.get("tipo", ""),
                    cliente=entry.get("cliente", ""),
                    refs=[tuple(r) for r in entry.get("refs", [])],
                    mtime=entry.get("mtime", 0.0),
                    aprobada=entry.get("aprobada", False),
                )
                self._docs[path] = doc
            self._rebuild_ref_map()
        except (json.JSONDecodeError, OSError):
            self._docs = {}

    def _save(self) -> None:
        INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        raw = {
            "files": {
                path: {
                    "title": d.title,
                    "tipo": d.tipo,
                    "cliente": d.cliente,
                    "refs": [list(r) for r in d.refs],
                    "mtime": d.mtime,
                    "aprobada": d.aprobada,
                }
                for path, d in self._docs.items()
            }
        }
        INDEX_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")

    def _rebuild_ref_map(self) -> None:
        self._by_ref = {}
        for path, doc in self._docs.items():
            for tag, art in doc.refs:
                self._by_ref.setdefault(f"{tag}:{art}", []).append(path)

    # ── Escaneo ──────────────────────────────────────────────────

    def _iter_md_files(self) -> list[Path]:
        files: list[Path] = []
        vault = config.OBSIDIAN_VAULT_PATH
        roots = [vault] if vault.exists() else []
        roots += [d for d in EXTRA_DIRS if d.exists()]
        for root in roots:
            for p in root.rglob("*.md"):
                if any(part in SKIP_DIRS for part in p.parts):
                    continue
                files.append(p)
        return files

    def rebuild(self, *, force: bool = False) -> dict[str, int]:
        """Reescanea los archivos (incremental por mtime salvo force=True)."""
        self._load()
        stats = {"scanned": 0, "updated": 0, "removed": 0}

        seen: set[str] = set()
        for path in self._iter_md_files():
            spath = str(path)
            seen.add(spath)
            stats["scanned"] += 1
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            existing = self._docs.get(spath)
            if not force and existing and existing.mtime >= mtime:
                continue

            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            meta = _parse_frontmatter(text)
            doc = VaultDoc(
                path=spath,
                title=meta.get("title", path.stem),
                tipo=meta.get("tipo", ""),
                cliente=meta.get("cliente", ""),
                refs=extract_article_refs(text),
                mtime=mtime,
                aprobada=_parse_bool(meta.get("aprobada", None)),
                _text=text,
            )
            self._docs[spath] = doc
            stats["updated"] += 1

        # Eliminar archivos que ya no existen
        for spath in list(self._docs):
            if spath not in seen:
                del self._docs[spath]
                stats["removed"] += 1

        self._rebuild_ref_map()
        self._save()
        return stats

    # ── Consulta ─────────────────────────────────────────────────

    def docs_for_refs(self, refs: list[tuple[str, str]], *, max_docs: int = 10) -> list[VaultDoc]:
        """Documentos que citan alguno de los articulos dados."""
        self._load()
        paths: list[str] = []
        for tag, art in refs:
            paths.extend(self._by_ref.get(f"{tag}:{art}", []))
        # dedupe preservando orden
        unique = list(dict.fromkeys(paths))
        return [self._docs[p] for p in unique[:max_docs] if p in self._docs]

    def search(self, query: str, *, max_docs: int = 8, cliente: str | None = None, aprobadas_only: bool = False) -> list[VaultDoc]:
        """Busqueda hibrida: citas exactas de articulo + keywords.

        1. Si la query menciona articulos ("art 200", "art. 17 N 8"), los docs
           que los citan reciben score alto.
        2. Keyword scoring sobre titulo + cuerpo como desempate.

        Si aprobadas_only=True, solo considera notas marcadas como aprobadas
        (conocimiento validado por el usuario).
        """
        self._load()
        refs = extract_article_refs(query)
        ref_paths = set()
        for tag, art in refs:
            ref_paths.update(self._by_ref.get(f"{tag}:{art}", []))
        # Numeros sueltos (sin ley en la query): matchean en cualquier cuerpo legal
        for num in extract_article_numbers(query):
            for tag in ("lir", "iva", "ct"):
                ref_paths.update(self._by_ref.get(f"{tag}:{num}", []))

        q_words = {
            w for w in re.findall(r"[a-záéíóúñ0-9]+", query.lower()) if len(w) > 2
        }
        stopwords = {"para", "sobre", "como", "del", "los", "las", "una", "que", "con", "por"}
        q_words -= stopwords

        scored: list[tuple[float, VaultDoc]] = []
        for path, doc in self._docs.items():
            if aprobadas_only and not doc.aprobada:
                continue
            if cliente and doc.cliente and doc.cliente.lower() != cliente.lower():
                continue
            score = 0.0
            if path in ref_paths:
                score += 100.0
            text = doc.load_text().lower()
            title = doc.title.lower()
            for w in q_words:
                if w in title:
                    score += 5.0
                elif w in text[:4000]:
                    score += 1.0
            if doc.aprobada:
                score += 2.0  # leve prioridad para notas aprobadas
            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:max_docs]]

    def pending_approval(self, *, max_docs: int = 100) -> list[VaultDoc]:
        """Notas del vault de tipos revisables que AUN no estan aprobadas."""
        self._load()
        revisables = {"jurisprudencia", "peticion", "analisis", "estudio", "nota"}
        result = sorted(
            (d for d in self._docs.values()
             if not d.aprobada and (d.tipo in revisables or not d.tipo)),
            key=lambda d: d.mtime,
            reverse=True,
        )
        return result[:max_docs]

    def set_aprobada(self, path: str, aprobada: bool) -> bool:
        """Marca/desmarca una nota como aprobada editando su frontmatter en disco."""
        self._load()
        doc = self._docs.get(path)
        if not doc:
            return False
        try:
            p = Path(path)
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

        if aprobada:
            # Añadir/reemplazar campo aprobada: true en el frontmatter
            head_match = re.match(r"^---\s*\n(.*?)\n---", text, flags=re.DOTALL)
            if head_match:
                new_head = _ensure_frontmatter_aprobada(head_match.group(1))
                body = "---\n" + new_head + "\n---" + text[head_match.end():]
            else:
                body = f"---\naprobada: true\ntags: [aprobada]\n---\n\n{text}"
        else:
            head_match = re.match(r"^---\s*\n(.*?)\n---", text, flags=re.DOTALL)
            if head_match:
                head = head_match.group(1)
                head = re.sub(r"^\s*aprobada:\s*.*$", "", head, flags=re.MULTILINE)
                # Quitar 'aprobada' del tag si corresponde
                tag_m = re.search(r"^tags:\s*\[(.*)\]", head, flags=re.MULTILINE)
                if tag_m:
                    tags = [t.strip() for t in tag_m.group(1).split(",") if t.strip()]
                    tags = [t for t in tags if t != "aprobada"]
                    head = head[:tag_m.start()] + f"tags: [{', '.join(tags)}]" + head[tag_m.end():]
                body = "---\n" + head.strip() + "\n---" + text[head_match.end():]
            else:
                body = text
        p.write_text(body, encoding="utf-8")

        # Actualizar índice
        doc.aprobada = aprobada
        try:
            doc.mtime = p.stat().st_mtime
        except OSError:
            pass
        doc._text = body
        self._save()
        return True

    def count_aprobadas(self) -> int:
        self._load()
        return sum(1 for d in self._docs.values() if d.aprobada)

    def approved_context(self, query: str, *, max_docs: int = 3, char_limit: int = 4000) -> tuple[str, list[dict]]:
        """Devuelve (texto_contexto, docs_usados) para las notas aprobadas relevantes.

        Se usa para inyectar conocimiento validado por el usuario en el prompt,
        con prioridad sobre la búsqueda web. Cada nota se etiqueta con su tipo,
        cliente y un aviso de que es conocimiento aprobado por el usuario.
        """
        docs = self.search(query, max_docs=max_docs, aprobadas_only=True)
        if not docs:
            return "", []

        parts: list[str] = []
        used: list[dict] = []
        for i, d in enumerate(docs, 1):
            body = d.load_text()
            # Limpiar el frontmatter y el header de título (se duplicaría con la etiqueta)
            body = re.sub(r"^---.*?---\s*", "", body, flags=re.DOTALL)
            body = re.sub(r"^#\s+.*\n+", "", body)  # '# Título' de write_note
            body = re.sub(r"^\#{1,3}\s+.*\n+", "", body)
            body = body.strip()
            if char_limit and len(body) > char_limit:
                body = body[:char_limit] + "\n[... truncado por longitud ...]"
            label = (d.tipo or "NOTA").upper()
            cliente = f" - Cliente: {d.cliente}" if d.cliente else ""
            parts.append(f"[NOTA APROBADA POR EL USUARIO #{i}]\nTipo: {label}{cliente}\nTítulo: {d.title}\n{body}")
            used.append({"path": d.path, "title": d.title, "tipo": d.tipo, "cliente": d.cliente})

        header = (
            "=== NOTAS APROBADAS (conocimiento validado por el usuario) ===\n"
            "Estas notas fueron revisadas y aprobadas por el tributarista. "
            "Si el tema de la consulta se resuelve en ellas, cítalas como fuente "
            "autorizada ANTES de dudar o de inventar algo.\n"
        )
        return header + "\n".join(parts) + "\n", used

    def stats(self) -> dict[str, int]:
        self._load()
        return {"docs": len(self._docs), "refs": len(self._by_ref)}


# Singleton
article_index = ArticleIndex()
