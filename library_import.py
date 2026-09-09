"""Importación trazable del archivo jurídico local ya descargado.

Los documentos preexistentes no se tratan como una respuesta del modelo: se
guardan como evidencia de SII/ACJ, con su identificador, fecha, URL oficial de
acceso y huella. La biblioteca activa conserva desde 2024; el archivo anterior
permanece disponible cuando una consulta requiere un precedente o una laguna.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import config
from production_store import ProductionStore, store


SII_ACJ_PORTAL = "https://www4.sii.cl/acjui/internet/"
_META = re.compile(r"^- ([\w_]+):\s*(.*?)\s*$", re.MULTILINE)
_URL = re.compile(r"https?://[^\s)]+", re.I)


def _metadata(text: str) -> dict[str, str]:
    start = text.find("## Metadata")
    end = text.find("\n## ", start + 2) if start >= 0 else -1
    section = text[start:end if end >= 0 else None]
    return {key.lower(): value.strip() for key, value in _META.findall(section)}


def _official_url(meta: dict[str, str], text: str) -> tuple[str, str]:
    """Devuelve una URL estable de la fuente y la localización comprobable."""
    for candidate in (meta.get("pdf_url", ""), *(_URL.findall(text))):
        if candidate.startswith("http") and "sii.cl" in candidate.lower():
            return candidate.rstrip(".,"), "Documento oficial SII"
    identifier = meta.get("jurisprudencia_id", "")
    if identifier and identifier.lower() != "n/a":
        return f"{SII_ACJ_PORTAL}#pronunciamiento-{identifier}", f"ACJ; pronunciamiento ID {identifier}"
    return SII_ACJ_PORTAL, "Portal público ACJ del SII; identificación no disponible"


def _legal_status(meta: dict[str, str]) -> str:
    status = " ".join((meta.get("estado_vigencia", ""), meta.get("dejada_sin_efecto_por", ""))).lower()
    return "derogada" if "derog" in status or "sin efecto" in status else "vigente"


def _complete_text(text: str, document_type: str) -> bool:
    """No presenta una ficha de índice como si fuera el original completo."""
    if document_type == "circular_sii":
        return False
    match = re.search(r"## Contenido\s*(.*?)(?:\n## Fuente|\Z)", text, re.S)
    return bool(match and len(match.group(1).strip()) >= 300)


def _body_without_import_metadata(text: str) -> str:
    """Compara el contenido jurídico sin la ficha variable artículo–ACJ."""
    return re.sub(r"## Metadata\s*.*?(?=\n## |\Z)", "", text, flags=re.S).strip()


def _iter_sii_documents(root: Path) -> Iterable[Path]:
    for folder in (root / "jurisprudencia_sii", root / "jurisprudencia_sii_circulares"):
        if folder.exists():
            yield from folder.rglob("*.md")


def import_existing_sii_documents(*, production_store: ProductionStore = store,
                                  root: Path | None = None) -> dict[str, int]:
    """Incorpora el archivo SII/ACJ local sin eliminar ni reescribir originales."""
    root = root or config.DOCUMENTS_DIR
    stats = {"scanned": 0, "imported": 0, "unchanged": 0, "current_2024_plus": 0, "historical_archive": 0,
             "skipped": 0}
    relations: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()
    for path in _iter_sii_documents(root):
        stats["scanned"] += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            stats["skipped"] += 1
            continue
        meta = _metadata(text)
        source_type = meta.get("source_type", "")
        if source_type != "jurisprudencia_sii":
            stats["skipped"] += 1
            continue
        document_id = meta.get("jurisprudencia_id", path.stem)
        fecha = meta.get("fecha", "")
        is_current = bool(re.match(r"^20(?:2[4-9]|[3-9]\d)-", fecha))
        url, location = _official_url(meta, text)
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem
        subtype = meta.get("jurisprudencia_subtype", "")
        document_type = "circular_sii" if "circular" in subtype or "circular" in title.lower() else "jurisprudencia_tributaria"
        metadata: dict[str, Any] = {
            "official": True,
            "origin": "archivo_local_verificado_sii",
            "document_id": document_id,
            "date": fecha or None,
            "archive_historical": not is_current,
            "complete_text": _complete_text(text, document_type),
            "location": location,
            "local_original": str(path),
            "cuerpo_normativo_id": meta.get("cuerpo_normativo_id", ""),
            "articulo_nombre": meta.get("articulo_nombre", ""),
            "codigo_pronunciamiento": meta.get("codigo_pronunciamiento", ""),
        }
        # ACJ publica una misma sentencia bajo cada artículo relacionado. El
        # original se conserva una sola vez y las demás apariciones alimentan
        # las relaciones, evitando versiones artificiales por el orden de
        # importación.
        first_occurrence = url not in seen_urls
        seen_urls.add(url)
        changed = None
        if first_occurrence:
            changed = production_store.upsert_source(
                url=url, source="SII/ACJ" if document_type == "jurisprudencia_tributaria" else "SII",
                document_type=document_type, title=title, legal_status=_legal_status(meta), body=text,
                effective_date=fecha or None, metadata=metadata,
            )
        cuerpo = meta.get("cuerpo_normativo_id", "")
        articulo = meta.get("articulo_nombre", "")
        if cuerpo or articulo:
            target = f"Cuerpo normativo {cuerpo or 'no identificado'}; artículo {articulo or 'no identificado'}"
            relations.append((url, "relacionado_con", target))
        codigo = meta.get("codigo_pronunciamiento", "")
        if codigo:
            relations.append((url, "identificado_como", codigo))
        if first_occurrence:
            if changed:
                stats["imported"] += 1
            else:
                stats["unchanged"] += 1
        stats["current_2024_plus" if is_current else "historical_archive"] += 1
    production_store.add_source_relations(relations)
    stats["unique_sources"] = len(seen_urls)
    production_store.audit("existing_sii_library_import", **stats, relations=len(relations))
    return stats


def normalize_sii_import_versions(*, production_store: ProductionStore = store) -> dict[str, int]:
    """Elimina sólo versiones artificiales de una misma sentencia importada.

    Una modificación real del texto, resumen o decisión sigue teniendo una
    huella distinta después de quitar la ficha de importación y por eso no se
    toca. Las relaciones artículo–sentencia ya residen en ``source_relations``.
    """
    removed_ids: list[str] = []
    with production_store._conn() as connection:
        sources = connection.execute("SELECT url,content_hash,body FROM official_sources WHERE source='SII/ACJ'").fetchall()
        for source in sources:
            current_core = _body_without_import_metadata(source["body"])
            versions = connection.execute("SELECT id,content_hash,body FROM source_versions WHERE url=?", (source["url"],)).fetchall()
            for version in versions:
                if version["content_hash"] != source["content_hash"] and _body_without_import_metadata(version["body"]) == current_core:
                    removed_ids.append(version["id"])
        if removed_ids:
            placeholders = ",".join("?" for _ in removed_ids)
            connection.execute(f"DELETE FROM source_versions WHERE id IN ({placeholders})", removed_ids)
    result = {"removed_artificial_versions": len(removed_ids)}
    production_store.audit("sii_import_versions_normalized", **result)
    return result
