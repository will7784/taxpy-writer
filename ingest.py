"""Ingesta automática de documentos de clientes (webhook tipo n8n / Drive).

Recibe un archivo (multipart) o la URL de un archivo (lo baja y lo deja en la
carpeta entrada/ del cliente del Co-Work) y, opcionalmente, dispara el pipeline
de procesamiento (OCR + análisis) para que quede consultable con el agente.
"""

from __future__ import annotations

import hmac
from pathlib import Path
from urllib.parse import unquote, urlparse

import config
from obsidian_writer import get_cliente_dir

INGEST_EXTS = {
    ".pdf", ".docx", ".txt", ".md",
    ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp",
}


def check_token(headers: dict) -> bool:
    """Valida el token de ingesta (X-API-Key o Bearer).

    Sin ``INGEST_TOKEN`` configurado queda abierto (solo desarrollo).
    """
    token = getattr(config, "INGEST_TOKEN", "") or ""
    if not token:
        return True
    raw = (headers.get("x-api-key") or headers.get("authorization") or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:]
    return bool(raw) and hmac.compare_digest(raw, token)


def guardar_entrada(
    cliente: str,
    filename: str,
    data: bytes,
    *,
    max_bytes: int = 25 * 1024 * 1024,
) -> Path:
    """Valida nombre/extensión y guarda ``data`` en entrada/ del cliente."""
    client_dir = get_cliente_dir(cliente)
    if not client_dir.is_dir():
        raise FileNotFoundError(f"Cliente no encontrado: {cliente}")
    parts = (filename or "").replace("\\", "/").split("/")
    if any(not p or p in {".", ".."} or any(c in p for c in ':<>"|?*') for p in parts):
        raise ValueError("Nombre de documento inválido")
    name = "__".join(parts)
    if Path(name).suffix.lower() not in INGEST_EXTS:
        raise ValueError("Formato no admitido. Usa PDF, DOCX, TXT, MD o una imagen (PNG/JPG/...).")
    if len(data) > max_bytes:
        raise ValueError(f"El documento supera el límite de {max_bytes // (1024 * 1024)} MB.")
    entrada = client_dir / "entrada"
    entrada.mkdir(exist_ok=True)
    target = entrada / name
    if target.exists():
        raise FileExistsError(f"Ya existe un documento con ese nombre; no se sobrescribió.")
    target.write_bytes(data)
    return target


def filename_from_url(url: str) -> str:
    """Deriva un nombre de archivo de la URL."""
    return Path(unquote(urlparse(url).path)).name or "documento"


async def fetch_url(url: str) -> bytes:
    """Descarga el contenido de una URL (para archivos de Google Drive / Dropbox)."""
    import httpx
    from http_security import tls_context
    async with httpx.AsyncClient(verify=tls_context(), timeout=180, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content
