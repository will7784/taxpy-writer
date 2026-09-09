"""Ingesta reproducible de fuentes juridicas oficiales chilenas."""
from __future__ import annotations

import asyncio
import hashlib
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from http_security import tls_context

import config
from production_store import store


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav", "footer"}: self.skip += 1
        if tag in {"p", "br", "li", "h1", "h2", "h3", "tr"}: self.parts.append("\n")
    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "footer"}: self.skip = max(0, self.skip - 1)
    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip(): self.parts.append(data.strip())
    def text(self) -> str: return "\n".join(self.parts)


class _Links(HTMLParser):
    """Extrae enlaces conservando una etiqueta legible cuando el portal la da."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._label: list[str] = []
        self._hint = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        self._href = values.get("href", "")
        self._hint = values.get("title", "") or values.get("aria-label", "")
        self._label = []

    def handle_data(self, data: str) -> None:
        if self._href and data.strip():
            self._label.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            label = self._hint or " ".join(self._label)
            self.links.append((self._href, re.sub(r"\s+", " ", label).strip()))
            self._href = ""; self._label = []; self._hint = ""


class _TableLinks(HTMLParser):
    """Asocia un enlace con la fila y fecha que informa el índice de sanciones."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, str]] = []
        self._in_row = False
        self._row_text: list[str] = []
        self._row_links: list[tuple[str, str]] = []
        self._href = ""; self._hint = ""; self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._in_row = True; self._row_text = []; self._row_links = []
        elif tag == "a" and self._in_row:
            values = {key.lower(): value or "" for key, value in attrs}
            self._href = values.get("href", "")
            self._hint = values.get("title", "") or values.get("aria-label", "")
            self._label = []

    def handle_data(self, data: str) -> None:
        if not self._in_row or not data.strip():
            return
        self._row_text.append(data.strip())
        if self._href:
            self._label.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self._row_links.append((self._href, self._hint or " ".join(self._label)))
            self._href = ""; self._hint = ""; self._label = []
        elif tag == "tr" and self._in_row:
            context = re.sub(r"\s+", " ", " ".join(self._row_text)).strip()
            self.links.extend((href, re.sub(r"\s+", " ", label).strip(), context)
                              for href, label in self._row_links)
            self._in_row = False


def _status(kind: str, body: str) -> str:
    text = body.lower()
    if kind == "proyecto" or "proyecto de ley" in text or "boletín" in text:
        if "publicada" not in text: return "proyecto_en_tramitacion"
    # Una circular, sentencia o sanción puede mencionar normas derogadas sin
    # que el documento mismo deje de ser una fuente pública consultable.
    if kind not in {"ley", "publicacion"}:
        return "vigente"
    if "derogada" in text: return "derogada"
    if "entrará en vigencia" in text or "entrara en vigencia" in text: return "publicada_pendiente_vigencia"
    if "modifica" in text or "reemplázase" in text or "reemplazase" in text: return "modificada"
    return "vigente"


def _title(html: str, fallback: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    return re.sub(r"\s+", " ", re.sub(r"<.*?>", "", m.group(1))).strip()[:500] if m else fallback


async def _ingest(client: httpx.AsyncClient, url: str, source: str, document_type: str,
                  title_hint: str | None = None, metadata_extra: dict[str, Any] | None = None) -> str | None:
    response = await client.get(url)
    response.raise_for_status()
    if response.content.startswith(b"%PDF"):
        def extract_pdf() -> str:
            import fitz
            with fitz.open(stream=response.content, filetype="pdf") as doc:
                return "\n".join(page.get_text() for page in doc)
        body = await asyncio.to_thread(extract_pdf)
        html = ""
    else:
        html = response.text
        parser = _Text(); parser.feed(html)
        body = parser.text()
    if len(body) < 80:
        raise ValueError("La fuente no contiene texto juridico util")
    page_title = _title(html, url)
    # LeyChile usa un título HTML genérico; el catálogo entrega el nombre
    # jurídico para que la cita sea legible sin sustituir el texto original.
    title = title_hint or page_title
    metadata = {"content_type": response.headers.get("content-type", ""), "official": True,
                "complete_text": True, "bytes": len(response.content)}
    metadata.update(metadata_extra or {})
    return store.upsert_source(url=url, source=source, document_type=document_type,
        title=title, legal_status=_status(document_type, body), body=body,
        metadata=metadata)


UAF_DISCOVERY_PAGES: tuple[tuple[str, str, str], ...] = (
    ("https://www.uaf.cl/es-cl/normativa/circulares-uaf", "circular_uaf", "Circulares UAF"),
    ("https://www.uaf.cl/es-cl/publicaciones-uaf/sanciones-ejecutoriadas", "sancion_uaf", "Sanción ejecutoriada UAF"),
    ("https://www.uaf.cl/es-cl/publicaciones-uaf/informe-de-tipologias", "tipologia_uaf", "Informe de tipologías UAF"),
)


def _uaf_catalog_key(url: str) -> str:
    return "uaf_document_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def _uaf_title(label: str, url: str, fallback: str) -> str:
    label = re.sub(r"\s+", " ", label).strip()
    if label and label.lower() not in {"descargar", "download", "ver documento"}:
        return label[:500]
    filename = urlparse(url).path.rsplit("/", 1)[-1]
    return (filename or fallback).replace("_", " ")[:500]


def _uaf_year(body: str) -> int | None:
    matches = re.findall(r"\b(20\d{2})\b", body[:8_000])
    return int(matches[0]) if matches else None


async def _discover_uaf_files(client: httpx.AsyncClient, start_url: str, *, from_year: int | None = None
                              ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Recorre paginación pública UAF sin declarar un total que el portal no entrega."""
    queued = [start_url]
    seen_pages: set[str] = set()
    files: dict[str, str] = {}
    archived: dict[str, str] = {}
    while queued:
        page = queued.pop(0)
        if page in seen_pages:
            continue
        seen_pages.add(page)
        response = await client.get(page)
        response.raise_for_status()
        parser = _Links(); parser.feed(response.text)
        table_parser = _TableLinks(); table_parser.feed(response.text)
        contexts = {urljoin(page, href): context for href, _label, context in table_parser.links}
        for href, label in parser.links:
            target = urljoin(page, href)
            parsed = urlparse(target)
            if parsed.scheme != "https" or not parsed.netloc.endswith("uaf.cl"):
                continue
            path = parsed.path.lower()
            if path.endswith(".pdf"):
                context = contexts.get(target, "")
                years = [int(year) for year in re.findall(r"\b(20\d{2})\b", context)]
                if from_year and years and max(years) < from_year:
                    archived.setdefault(target, label)
                else:
                    files.setdefault(target, label)
            # Sólo se siguen enlaces de paginación en la misma colección;
            # menús y enlaces a otras materias no expanden el universo.
            elif parsed.path == urlparse(start_url).path and parsed.query and target not in seen_pages:
                queued.append(target)
    return list(files.items()), list(archived.items())


async def _sync_uaf_documents(client: httpx.AsyncClient, changes: list[str], errors: list[str]) -> int:
    """Descubre y lee PDFs UAF; cada archivo obtiene estado propio de cobertura."""
    documents: dict[str, tuple[str, str, str]] = {}
    for start_url, document_type, fallback_title in UAF_DISCOVERY_PAGES:
        try:
            # Las sanciones se mantienen permanentemente desde 2024; las
            # anteriores quedan descubiertas para ser incorporadas a demanda.
            minimum = 2024 if document_type == "sancion_uaf" else None
            files, archived = await _discover_uaf_files(client, start_url, from_year=minimum)
        except Exception as exc:
            errors.append(f"{start_url}: descubrimiento UAF {type(exc).__name__}: {exc}")
            continue
        for url, label in files:
            documents.setdefault(url, (document_type, _uaf_title(label, url, fallback_title), start_url))
        for url, label in archived:
            key = _uaf_catalog_key(url)
            store.register_library_catalog([{"key": key, "source": "UAF", "document_type": document_type,
                                             "title": _uaf_title(label, url, fallback_title), "url": url,
                                             "collection": "uaf"}])
    store.register_library_catalog([{"key": _uaf_catalog_key(url), "source": "UAF", "document_type": kind,
                                     "title": title, "url": url, "collection": "uaf"}
                                    for url, (kind, title, _origin) in documents.items()])
    semaphore = asyncio.Semaphore(max(1, config.RESEARCH_FETCH_CONCURRENCY))

    async def ingest_one(url: str, kind: str, title: str, origin: str) -> str | None:
        key = _uaf_catalog_key(url)
        async with semaphore:
            try:
                change = await _ingest(client, url, "UAF", kind, title,
                                       {"discovered_from": origin, "collection": "uaf"})
                store.record_library_attempt(key, downloaded=True, indexed=True)
                return change
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
                store.record_library_attempt(key, downloaded=False, indexed=False, error=f"{type(exc).__name__}: {exc}")
                return None

    completed = await asyncio.gather(*(ingest_one(url, kind, title, origin)
                                       for url, (kind, title, origin) in documents.items()))
    changes.extend(change for change in completed if change)
    return len(documents)


async def sync_official_sources() -> dict[str, Any]:
    """Sincroniza URLs configuradas; no interpreta fuentes secundarias como derecho."""
    from legal_library import catalog_entries
    catalog = catalog_entries()
    store.register_library_catalog(catalog)
    configured = ([(u, "BCN/LeyChile", "ley", None, None) for u in config.BCN_LEYCHILE_URLS] +
               [(u, "Congreso/BCN", "proyecto", None, None) for u in config.CONGRESS_SOURCE_URLS] +
               [(u, "Diario Oficial", "publicacion", None, None) for u in config.DIARIO_OFICIAL_URLS] +
               [(u, "SII", "jurisprudencia_administrativa", None, None) for u in config.SII_OFFICIAL_URLS])
    entries = [(entry["url"], entry["source"], entry["document_type"], entry["key"], entry["title"])
               for entry in catalog]
    # Las variables de entorno sirven para añadir fuentes sin editar código.
    # Se deduplican por URL para no descargar dos veces una fuente catalogada.
    entries += configured
    unique: dict[str, tuple[str, str, str, str | None, str | None]] = {entry[0]: entry for entry in entries}
    run_id = store.begin_sync(); changes: list[str] = []; errors: list[str] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, verify=tls_context(), headers={"User-Agent": "ImpuestIA/official-source-monitor"}) as client:
        for url, source, kind, catalog_key, title_hint in unique.values():
            try:
                change = await _ingest(client, url, source, kind, title_hint)
                if change: changes.append(change)
                if catalog_key:
                    store.record_library_attempt(catalog_key, downloaded=True, indexed=True)
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
                if catalog_key:
                    store.record_library_attempt(catalog_key, downloaded=False, indexed=False, error=f"{type(exc).__name__}: {exc}")
        uaf_checked = await _sync_uaf_documents(client, changes, errors)
    checked = len(unique) + uaf_checked
    store.finish_sync(run_id, checked=checked, changes=len(changes), error="; ".join(errors))
    store.audit("official_sync", run_id, checked=checked, changes=len(changes), errors=errors)
    return {"run_id": run_id, "checked": checked, "changes": len(changes), "errors": errors,
            "coverage": store.library_coverage()}


def official_evidence(query: str, limit: int = 6) -> tuple[str, list[dict[str, Any]]]:
    docs = store.search_official(query, limit)
    if not docs: return "", []
    parts = ["=== EVIDENCIA OFICIAL ACTUALIZADA ==="]
    for i, d in enumerate(docs, 1):
        parts.append(f"[Fuente {i}: {d['document_type']} | estado: {d['legal_status']} | consultada: {d['retrieved_at']}]\n"
                     f"Título: {d['title']}\nURL: {d['url']}\nExtracto: {d['extract']}")
    return "\n\n".join(parts), docs


def should_check_official(query: str) -> bool:
    signals = ("reforma", "proyecto de ley", "vigencia", "vigente", "última modificación", "ultima modificacion",
               "nuevo oficio", "nueva circular", "actualización", "actualizacion", "ley reciente", "diario oficial")
    return any(s in query.lower() for s in signals)


def run_sync() -> dict[str, Any]:
    return asyncio.run(sync_official_sources())
