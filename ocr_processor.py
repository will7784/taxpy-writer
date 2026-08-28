"""
OCR Processor — convierte material escaneado o digital a texto/Markdown.

Flujo segun el tipo de archivo:
  - .txt / .md / texto plano   -> lectura directa
  - .docx                      -> python-docx (parrafos)
  - .pdf con texto             -> pdfplumber (extraccion nativa, por pagina)
  - .pdf escaneado (sin texto) -> rasteriza paginas con PyMuPDF y OCR
                                  vision (GPT-4o) por pagina
  - imagenes (png/jpg/...)     -> OCR vision (GPT-4o)

El OCR usa la API de OpenAI (config.OPENAI_API_KEY / OPENAI_MODEL, p.ej.
gpt-4o) con un prompt de transcripcion FIEL: conserva numeros de articulo,
cifras (UF/UTM/UTA, %) y estructura. El modelo de vision es un OCR potente
para el texto legal chileno: no requiere instalar Tesseract/poppler.

La salida es Markdown. Ademas se devuelven las citas legales detectadas
(ley, numero de articulo) para indexarlas con article_index.

Uso:
    from ocr_processor import extract_markdown
    md, meta = await extract_markdown(path)
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

import config

console = Console()

# Un PDF con menos de este texto por pagina se considera escaneado
MIN_TEXT_PER_PAGE = 50
MAX_OCR_PAGES = 30  # limite de paginas OCR por documento (coste/tiempo)

_VISION_SYSTEM = (
    "Eres un OCR especializado en documentos legales y tributarios chilenos "
    "(leyes, oficios y circulares del SII, sentencias, escrituras, formularios). "
    "Transcribe la imagen a Markdown FIELMENTE.\n"
    "REGLAS:\n"
    "1. Conserva EXACTAMENTE los numeros de articulo, inciso, numeral, letra "
    "(ej: 'Art. 14 letra D)', 'Art. 17 N 8') y las cifras (UF, UTM, UTA, %, "
    "fechas, montos).\n"
    "2. No resumas, no interpretes, no agregues texto que no este en la imagen.\n"
    "3. No inventes normas ni completaciones. Si hay una zona ilegible, marca "
    "[ilegible].\n"
    "4. Respeta titulos y parrafos con Markdown (## para encabezados).\n"
    "5. Si detectas tablas, transcribelas como tablas Markdown.\n"
    "6. Devuelve SOLO el texto Markdown transcrito, sin bloques de codigo "
    "(no envuelvas en ```), sin comentarios tuyos, sin saludo inicial."
)

IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "tiff", "tif", "bmp"}


def _mime_for(ext: str) -> str:
    return {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "tiff": "image/tiff", "tif": "image/tiff",
        "bmp": "image/bmp",
    }.get(ext, "image/png")


async def _ocr_image_bytes(data: bytes, mime: str, llm=None) -> str:
    """OCR de una imagen con vision GPT-4o (o el modelo configurado)."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    b64 = base64.b64encode(data).decode()
    resp = await client.chat.completions.create(
        model=config.OPENAI_MODEL,
        temperature=0.0,
        max_tokens=4000,
        messages=[
            {"role": "system", "content": _VISION_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe este documento a Markdown."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            },
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _pdf_native_text(path: Path) -> tuple[list[str], bool]:
    """Extrae texto por pagina con pdfplumber. Retorna (paginas, parece_escaneado)."""
    import pdfplumber
    pages: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for p in pdf.pages:
            pages.append(p.extract_text() or "")
    total = sum(len(p) for p in pages)
    per_page = total / max(len(pages), 1)
    scanned = per_page < MIN_TEXT_PER_PAGE
    return pages, scanned


def _pdf_render_pages(path: Path, max_pages: int = MAX_OCR_PAGES) -> list[bytes]:
    """Rasteriza las paginas del PDF a PNG (para OCR) con PyMuPDF."""
    import fitz
    imgs: list[bytes] = []
    with fitz.open(str(path)) as doc:
        for i in range(min(doc.page_count, max_pages)):
            pix = doc[i].get_pixmap(dpi=200)
            imgs.append(pix.tobytes("png"))
    return imgs


async def extract_markdown(path: str | Path, *, max_pages: int = MAX_OCR_PAGES) -> tuple[str, dict[str, Any]]:
    """Convierte un archivo (txt/md/docx/pdf/imagen) a Markdown.

    Returns:
        (markdown, meta) con meta = {pages, ocr, tipo_src, chars}
    """
    p = Path(path)
    ext = p.suffix.lower().lstrip(".")
    meta: dict[str, Any] = {"pages": 1, "ocr": False, "tipo_src": ext, "chars": 0}

    if not config.OPENAI_API_KEY and ext in (IMAGE_EXTS | {"pdf"}):
        raise RuntimeError(
            "Para OCR se requiere OPENAI_API_KEY (vision GPT-4o)."
        )

    if ext in ("txt", "md"):
        text = p.read_text(encoding="utf-8", errors="replace")
        meta["chars"] = len(text)
        return text, meta

    if ext == "docx":
        from docx import Document
        doc = Document(str(p))
        text = "\n".join(par.text for par in doc.paragraphs if par.text.strip())
        meta["chars"] = len(text)
        return text, meta

    if ext == "pdf":
        pages, scanned = _pdf_native_text(p)
        if not scanned:
            meta["pages"] = len(pages)
            meta["chars"] = sum(len(x) for x in pages)
            return "\n\n---\n\n".join(pages), meta

        # PDF escaneado -> rasterizar y OCR vision por pagina
        console.print(f"[cyan][OCR] PDF escaneado detectado ({p.name}) — OCR de {min(len(pages), max_pages)} paginas...[/cyan]")
        imgs = _pdf_render_pages(p, max_pages=max_pages)
        out: list[str] = []
        for i, img in enumerate(imgs):
            md_page = await _ocr_image_bytes(img, "image/png")
            out.append(f"<!-- pagina {i + 1} (OCR) -->\n\n{md_page}")
        text = "\n\n---\n\n".join(out)
        meta.update({"pages": len(out), "ocr": True, "chars": len(text)})
        return text, meta

    if ext in IMAGE_EXTS:
        console.print(f"[cyan][OCR] Imagen ({p.name}) — OCR vision...[/cyan]")
        text = await _ocr_image_bytes(p.read_bytes(), _mime_for(ext))
        meta.update({"ocr": True, "chars": len(text)})
        return text, meta

    raise ValueError(f"Formato no soportado: .{ext}")
