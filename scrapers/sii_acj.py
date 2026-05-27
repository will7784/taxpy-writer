"""
Scraper de Jurisprudencia Judicial del SII (Área de Coordinación Jurídica - ACJ).

Endpoints:
- listCuerposNormativos
- findArticulos
- findPronunciamientos
- getFullPronunciamiento

Nota: La API del SII ACJ requiere una sesión válida. Si las requests directas fallan,
se puede usar Playwright para obtener cookies de sesión primero.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

import config

console = Console()

# URL base del servicio ACJ
ACJ_BASE_URL = "https://www4.sii.cl/acjui/internet/services/data/internetService"
ACJ_LEGACY_URL = "https://www4.sii.cl/acjui/services/InternetApplicationService"

# Headers estándar para evitar bloqueos
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CL,es;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www4.sii.cl",
    "Referer": "https://www4.sii.cl/acjui/internet/",
}


class SIIACJScraper:
    """Scraper de jurisprudencia judicial del SII ACJ."""

    def __init__(self, output_dir: Path | None = None) -> None:
        self.output_dir = output_dir or (config.DOCUMENTS_DIR / "jurisprudencia_sii")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._client: httpx.AsyncClient | None = None
        self._session_cookies: dict[str, str] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=DEFAULT_HEADERS,
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
            )
        return self._client

    async def _post(
        self, endpoint: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Hace POST a un endpoint del SII."""
        client = await self._get_client()

        # Intentar formato REST primero
        urls_to_try = [
            f"{ACJ_BASE_URL}/{endpoint}",
            ACJ_LEGACY_URL,
        ]

        for url in urls_to_try:
            try:
                response = await client.post(url, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    if data and not self._is_error_response(data):
                        return data
            except Exception as e:
                console.print(f"[dim]  POST {url} falló: {e}[/dim]")
                continue

        return None

    @staticmethod
    def _is_error_response(data: dict) -> bool:
        """Detecta si la respuesta es un error."""
        if not isinstance(data, dict):
            return True
        # Algunos errores vienen como {"error": "..."}
        if "error" in data:
            return True
        return False

    async def list_cuerpos_normativos(self) -> list[dict[str, Any]]:
        """Lista los cuerpos normativos disponibles (LIR, CT, IVA, etc.)."""
        payload = {
            "metaData": {
                "namespace": "cl.sii.sdi.lob.juridica.acj.data.impl.InternetApplicationService/listCuerposNormativos",
                "conversationId": "1",
                "transactionId": "taxpy-sync",
                "page": None,
            },
            "data": {},
        }
        data = await self._post("cuerpos-normativos", payload)
        if data and "data" in data:
            return data["data"]
        return []

    async def find_articulos(self, cuerpo_normativo_id: int) -> list[dict[str, Any]]:
        """Lista los artículos de un cuerpo normativo."""
        payload = {
            "metaData": {
                "namespace": "cl.sii.sdi.lob.juridica.acj.data.impl.InternetApplicationService/findArticulos",
                "conversationId": "1",
                "transactionId": f"taxpy-art-{cuerpo_normativo_id}",
                "page": None,
            },
            "data": {"id": cuerpo_normativo_id},
        }
        data = await self._post("find-articulos", payload)
        if data and "data" in data:
            return data["data"]
        return []

    async def find_pronunciamientos(
        self,
        articulo_id: int,
        cuerpo_normativo_id: int | None = None,
        tipo_instancia_id: int = 1,
        max_results: int = 2000,
    ) -> list[dict[str, Any]]:
        """Busca pronunciamientos por artículo."""
        payload = {
            "metaData": {
                "namespace": "cl.sii.sdi.lob.juridica.acj.data.impl.InternetApplicationService/findPronunciamientos",
                "conversationId": "1",
                "transactionId": f"taxpy-pron-{articulo_id}",
                "page": None,
            },
            "data": {
                "text": None,
                "tipoInstanciaId": tipo_instancia_id,
                "grupoInstanciaId": None,
                "tipoCodigoId": None,
                "codigo": None,
                "ruc": None,
                "instanciaId": None,
                "tipoPronunciamientoId": None,
                "cuerpoNormativoId": cuerpo_normativo_id,
                "articulosIds": [articulo_id],
                "reemplazos": [],
                "fechaDesde": None,
                "fechaHasta": None,
            },
        }
        data = await self._post("find-pronunciamientos", payload)
        if data and "data" in data:
            results = data["data"]
            # A veces viene paginado
            if isinstance(results, dict) and "list" in results:
                return results["list"][:max_results]
            if isinstance(results, list):
                return results[:max_results]
        return []

    async def get_full_pronunciamiento(self, pron_id: int) -> dict[str, Any] | None:
        """Obtiene el detalle completo de un pronunciamiento."""
        payload = {
            "metaData": {
                "namespace": "cl.sii.sdi.lob.juridica.acj.data.impl.InternetApplicationService/getFullPronunciamiento",
                "conversationId": "1",
                "transactionId": f"taxpy-full-{pron_id}",
                "page": None,
            },
            "data": {"id": pron_id},
        }
        data = await self._post("pronunciamientos/get-full", payload)
        if data and "data" in data:
            return data["data"]
        return None

    async def download_pdf(self, url: str, dest_path: Path) -> bool:
        """Descarga un PDF desde una URL del SII."""
        if not url or url == "N/A":
            return False
        try:
            client = await self._get_client()
            response = await client.get(url, timeout=30.0)
            if response.status_code == 200 and len(response.content) > 1000:
                dest_path.write_bytes(response.content)
                return True
        except Exception as e:
            console.print(f"[yellow]⚠️ Error descargando PDF {url}: {e}[/yellow]")
        return False

    @staticmethod
    def pron_to_md(pron_data: dict[str, Any], articulo_nombre: str = "") -> str:
        """Convierte un pronunciamiento JSON a formato Markdown."""
        data = pron_data.get("data", pron_data)
        if not data:
            return ""

        pron_id = data.get("id", "")
        codigo = data.get("codigo", "")
        fecha = data.get("fecha", "")
        tipo = data.get("tipoPronunciamiento", {}).get("nombre", "")
        instancia = data.get("instancia", {}).get("nombre", "")
        contenido = data.get("texto", "")
        resumen = data.get("resumen", "")
        pdf_url = data.get("urlDocumento", "") or "N/A"

        # Formatear fecha
        fecha_str = ""
        if fecha:
            try:
                dt = datetime.fromisoformat(fecha.replace("Z", "+00:00"))
                fecha_str = dt.strftime("%Y-%m-%d")
            except Exception:
                fecha_str = str(fecha)

        lines = [
            f"# Jurisprudencia SII - {codigo}",
            "",
            "## Metadata",
            f"- source_type: jurisprudencia_sii",
            f"- jurisprudencia_id: {pron_id}",
            f"- cuerpo_normativo_id: 2",
            f"- articulo_id: {data.get('articuloId', '')}",
            f"- articulo_nombre: {articulo_nombre}",
            f"- fecha: {fecha_str}",
            f"- tipo_pronunciamiento: {tipo}",
            f"- instancia: {instancia}",
            f"- codigo_pronunciamiento: {codigo}",
            f"- pdf_url: {pdf_url}",
            "- pdf_descargado: no",
            "",
            "## Resumen",
            resumen or ".",
            "",
            "## Contenido",
            contenido or ".",
            "",
            "## Fuente",
            f"- Servicio de Impuestos Internos (SII) - ACJ",
            f"- Pronunciamiento ID: {pron_id}",
        ]
        return "\n".join(lines)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
