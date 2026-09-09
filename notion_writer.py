"""Publicación de informes en Notion vía la API (integraciones internas).

Crea una página en una base de datos de Notion a partir de un texto (markdown
ligero) y devuelve la URL de la página. Es la salida "Fase 4" del pipeline:
el agente produce el informe y este módulo lo publica en Notion.

Requiere en el entorno (ver `.env`):
    NOTION_API_KEY       (token de integración interna: "ntn_..." o "secret_...")
    NOTION_DATABASE_ID   (id de la base de datos destino)
    NOTION_TITLE_PROPERTY (propiedad tipo título, default "Name")
"""

from __future__ import annotations

import re

import httpx

import config
from http_security import tls_context

API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
MAX_CHILDREN = 100  # límite por request de la API de Notion


def _rich(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text}}]


def _paragraph(text: str) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": _rich(text)}}


def _heading(text: str, level: int) -> dict:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": _rich(text)}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rich(text)}}


def _numbered(text: str) -> dict:
    return {"object": "block", "type": "numbered_list_item",
            "numbered_list_item": {"rich_text": _rich(text)}}


def make_blocks(text: str) -> list[dict]:
    """Convierte markdown ligero en bloques de Notion."""
    blocks: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("### "):
            blocks.append(_heading(line[4:], 3))
        elif line.startswith("## "):
            blocks.append(_heading(line[3:], 2))
        elif line.startswith("# "):
            blocks.append(_heading(line[2:], 1))
        elif line.startswith(("- ", "* ", "• ")):
            blocks.append(_bullet(line[2:]))
        elif re.match(r"^\d+[.)]\s*", line):
            blocks.append(_numbered(re.sub(r"^\d+[.)]\s*", "", line)))
        else:
            blocks.append(_paragraph(line))
    return blocks


async def publish_page(titulo: str, contenido: str, *, database_id: str | None = None) -> str:
    """Crea una página en Notion con `titulo` y `contenido`, devuelve su URL."""
    token = getattr(config, "NOTION_API_KEY", "") or ""
    db_id = database_id or getattr(config, "NOTION_DATABASE_ID", "") or ""
    if not token:
        raise ValueError("NOTION_API_KEY no está configurado.")
    if not db_id:
        raise ValueError("NOTION_DATABASE_ID no está configurado.")

    title_prop = getattr(config, "NOTION_TITLE_PROPERTY", "Name") or "Name"
    blocks = make_blocks(contenido)
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    payload = {
        "parent": {"database_id": db_id},
        "properties": {title_prop: {"title": [{"text": {"content": titulo}}]}},
        "children": blocks[:MAX_CHILDREN],
    }

    async with httpx.AsyncClient(verify=tls_context(), timeout=60) as client:
        resp = await client.post(f"{API_URL}/pages", json=payload, headers=headers)
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(f"Notion error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        page_id = data.get("id", "")

        rest = blocks[MAX_CHILDREN:]
        for i in range(0, len(rest), MAX_CHILDREN):
            batch = rest[i:i + MAX_CHILDREN]
            append = await client.patch(
                f"{API_URL}/blocks/{page_id}/children",
                json={"children": batch},
                headers=headers,
            )
            if append.status_code not in (200, 201):
                raise RuntimeError(f"Notion append error {append.status_code}: {append.text[:300]}")

    return data.get("url", "") or data.get("public_url", "")
