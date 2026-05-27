"""
Scraper de Circulares del SII.

La página de circulares del SII es pública y no requiere autenticación:
https://www.sii.cl/normativa_legislacion/circulares/
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

import config

console = Console()

CIRCULARES_BASE_URL = "https://www.sii.cl/normativa_legislacion/circulares/"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CL,es;q=0.9",
}


class SIICircularesScraper:
    """Scraper de circulares del SII."""

    def __init__(self, output_dir: Path | None = None) -> None:
        self.output_dir = output_dir or (config.DOCUMENTS_DIR / "jurisprudencia_sii_circulares")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=DEFAULT_HEADERS,
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
            )
        return self._client

    async def list_years(self) -> list[int]:
        """Lista los años disponibles de circulares."""
        try:
            client = await self._get_client()
            response = await client.get(CIRCULARES_BASE_URL)
            if response.status_code != 200:
                return list(range(2015, datetime.now().year + 1))

            # Buscar enlaces a años
            years = []
            for match in re.finditer(r'href="(\d{4})/', response.text):
                year = int(match.group(1))
                years.append(year)
            return sorted(set(years)) if years else list(range(2015, datetime.now().year + 1))
        except Exception as e:
            console.print(f"[yellow]⚠️ Error listando años: {e}[/yellow]")
            return list(range(2015, datetime.now().year + 1))

    async def scrape_year(self, year: int) -> list[dict[str, Any]]:
        """Scrapea todas las circulares de un año."""
        url = f"{CIRCULARES_BASE_URL}{year}/"
        results: list[dict[str, Any]] = []

        try:
            client = await self._get_client()
            response = await client.get(url)
            if response.status_code != 200:
                console.print(f"[yellow]⚠️ No se pudo acceder a {url}[/yellow]")
                return results

            html = response.text
            # Buscar enlaces a circulares: circu1.pdf, circu2.pdf, etc.
            pattern = re.compile(
                r'href="(circu(\d+)\.pdf)"[^>]*>(?:[^<]*<[^>]*>)?([^<]*)',
                re.IGNORECASE,
            )

            for match in pattern.finditer(html):
                pdf_name = match.group(1)
                numero = match.group(2)
                titulo = match.group(3).strip()

                pdf_url = f"{url}{pdf_name}"
                circular_id = f"circular-{year}-{numero}"

                results.append({
                    "circular_id": circular_id,
                    "year": year,
                    "numero": numero,
                    "titulo": titulo,
                    "pdf_url": pdf_url,
                    "pdf_name": pdf_name,
                })

        except Exception as e:
            console.print(f"[yellow]⚠️ Error scrapeando año {year}: {e}[/yellow]")

        return results

    def circular_to_md(self, circular: dict[str, Any]) -> str:
        """Convierte datos de una circular a formato Markdown."""
        circular_id = circular["circular_id"]
        year = circular["year"]
        numero = circular["numero"]
        titulo = circular.get("titulo", "")
        pdf_url = circular["pdf_url"]

        lines = [
            f"# Jurisprudencia Administrativa SII - CIRCULAR {numero}-{year}",
            "",
            "## Metadata",
            "- source_type: jurisprudencia_sii",
            "- jurisprudencia_subtype: circular_sii_web",
            "- source_name: sii_normativa_circulares",
            f"- jurisprudencia_id: {circular_id}",
            "- cuerpo_normativo_id_filter: N/A",
            "- articulo_nombre: N/A",
            "- articulo_filter: N/A",
            "- articulos_relacionados: N/A",
            f"- fecha: {year}-01-01",
            "- tipo_pronunciamiento: Circular",
            f"- instancia: Fuente: {titulo or 'Departamento de Impuestos Internos'}",
            f"- codigo_pronunciamiento: CIRCULAR {numero}-{year}",
            f"- pdf_url: {pdf_url}",
            "- estado_vigencia: vigente",
            "- dejada_sin_efecto_por: N/A",
            "- vigencia_fuente: N/A",
            "",
            "## Resumen",
            titulo or f"Circular N° {numero} del año {year}.",
            "",
            "## Contenido",
            f"Título: Circular N° {numero} del año {year}",
            "",
            f"Resumen: {titulo or 'Consultar documento PDF original.'}",
            "",
            f"Fuente índice: {CIRCULARES_BASE_URL}{year}/",
            f"Documento: {pdf_url}",
            "",
            "## Fuente",
            "- Servicio de Impuestos Internos (SII) - Normativa y Legislación",
            f"- Índice anual: {CIRCULARES_BASE_URL}{year}/",
        ]
        return "\n".join(lines)

    async def download_pdf(self, circular: dict[str, Any]) -> bool:
        """Descarga el PDF de una circular."""
        pdf_url = circular.get("pdf_url", "")
        if not pdf_url:
            return False

        year = circular["year"]
        pdf_name = circular["pdf_name"]
        pdf_dir = self.output_dir / "_pdf" / str(year)
        pdf_dir.mkdir(parents=True, exist_ok=True)
        dest_path = pdf_dir / pdf_name

        if dest_path.exists():
            return True

        try:
            client = await self._get_client()
            response = await client.get(pdf_url, timeout=30.0)
            if response.status_code == 200 and len(response.content) > 1000:
                dest_path.write_bytes(response.content)
                return True
        except Exception as e:
            console.print(f"[yellow]⚠️ Error descargando {pdf_url}: {e}[/yellow]")

        return False

    async def sync_circulares(self, years: list[int] | None = None) -> dict[str, int]:
        """Sincroniza todas las circulares de los años especificados."""
        if years is None:
            years = await self.list_years()

        total_md = 0
        total_pdf = 0

        for year in years:
            console.print(f"[blue]📅 Procesando circulares {year}...[/blue]")
            circulares = await self.scrape_year(year)
            console.print(f"  Encontradas: {len(circulares)}")

            year_dir = self.output_dir / str(year)
            year_dir.mkdir(parents=True, exist_ok=True)

            for circular in circulares:
                # Guardar .md
                md_content = self.circular_to_md(circular)
                md_path = year_dir / f"sii_circular_{year}_{circular['numero']}.md"
                md_path.write_text(md_content, encoding="utf-8")
                total_md += 1

                # Descargar PDF
                if await self.download_pdf(circular):
                    total_pdf += 1

        console.print(f"[green]✅ Circulares sincronizadas: {total_md} .md, {total_pdf} PDFs[/green]")
        return {"md": total_md, "pdfs": total_pdf}

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
