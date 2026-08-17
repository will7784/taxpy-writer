"""
Extrae texto limpio de las tres leyes desde los PDFs oficiales.
Genera los archivos .txt para el context-rag.
"""

import re
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).parent.parent
DOCUMENTS = ROOT / "documents"
OUTPUT = ROOT / "knowledge" / "laws"
OUTPUT.mkdir(parents=True, exist_ok=True)

LAW_FILES = {
    "dl824_lir": "DL-824_31-DIC-1974.pdf",
    "dl825_iva": "DL-825_31-DIC-1974.pdf",
    "dl830_ct": "DL-830_31-DIC-1974_codigo tributario.pdf",
}

LAW_NAMES = {
    "dl824_lir": "Ley sobre Impuesto a la Renta (DL-824)",
    "dl825_iva": "Ley sobre Impuesto a las Ventas y Servicios (DL-825)",
    "dl830_ct": "Codigo Tributario (DL-830)",
}


def is_footer_line(line: str) -> bool:
    stripped = line.strip()
    markers = [
        "Decreto Ley", "Biblioteca del Congreso", "www.leychile.cl",
        "documento generado", "pagina", "página",
    ]
    return any(m.lower() in stripped.lower() for m in markers)


def is_footnote_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    patterns = [
        r'^Ley\s+\d+$', r'^LEY\s+\d+$',
        r'^Art\.?\s+\w+\s+N[°\s]?\s*\d+',
        r'^Art\.?\s+\d+\s+N[°\s]?\s*\d+',
        r'^D\.O\.\s*\d{2}\.\d{2}\.\d{4}$',
        r'^NOTA\s*:?\s*\d*$',
        r'^DL\s+\d+.*HACIENDA$',
        r'^Decreto\s+\d+.*EXENTO',
    ]
    for p in patterns:
        if re.match(p, stripped, re.IGNORECASE):
            return True
    if len(stripped) < 40 and re.search(r'(Ley|D\.O\.|NOTA|DL|Decreto)', stripped):
        return True
    return False


def clean_text(text: str) -> str:
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        if is_footer_line(line):
            continue
        if is_footnote_line(line):
            continue
        cleaned.append(line)
    text = '\n'.join(cleaned)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def extract_pdf(pdf_path: Path) -> str:
    with pdfplumber.open(str(pdf_path)) as pdf:
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
    return clean_text(full_text)


def build_article_index(text: str) -> dict[str, tuple[int, int]]:
    """Construye indice de articulos: {numero: (offset_inicio, offset_fin)}."""
    index: dict[str, tuple[int, int]] = {}

    pattern = r'(ARTICULO|Artículo)\s+(\d+[\w°º]*)\.?\s*-?'
    matches = list(re.finditer(pattern, text, re.IGNORECASE))

    for i, m in enumerate(matches):
        num = m.group(2).upper()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        index[num] = (start, end)

    # Tambien buscar articulos transitorios
    trans = re.search(r'ART[IÍ]CULOS?\s+TRANSITORIOS?', text, re.IGNORECASE)
    if trans:
        trans_text = text[trans.start():]
        trans_matches = list(re.finditer(r'(ARTICULO|Artículo)\s+(\d+[\w°º]*)\.?\s*-?', trans_text, re.IGNORECASE))
        for i, m in enumerate(trans_matches):
            num = f"TRANS_{m.group(2).upper()}"
            start = trans.start() + m.start()
            end = trans.start() + (trans_matches[i + 1].start() if i + 1 < len(trans_matches) else len(trans_text))
            index[num] = (start, end)

    return index


def count_tokens_approx(text: str) -> int:
    """Estimacion gruesa: ~3 chars por token en español juridico."""
    return len(text) // 3


def main():
    for law_id, pdf_filename in LAW_FILES.items():
        pdf_path = DOCUMENTS / pdf_filename
        if not pdf_path.exists():
            print(f"[SKIP] {pdf_filename} no encontrado en documents/")
            continue

        print(f"[EXTRACT] {LAW_NAMES[law_id]}...")
        text = extract_pdf(pdf_path)

        out_path = OUTPUT / f"{law_id}.txt"
        out_path.write_text(text, encoding="utf-8")
        print(f"  [OK] {out_path} ({len(text):,} chars, ~{count_tokens_approx(text):,} tokens)")

        index = build_article_index(text)
        idx_path = OUTPUT / f"{law_id}.json"
        import json
        idx_path.write_text(json.dumps({k: list(v) for k, v in index.items()}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [IDX] {len(index)} articulos indexados -> {idx_path}")


if __name__ == "__main__":
    main()
