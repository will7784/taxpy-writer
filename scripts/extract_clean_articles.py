"""
Extractor limpio de articulos criticos desde PDFs oficiales.

Elimina:
- Encabezados y pies de pagina
- Notas al pie de modificaciones
- Texto corrupto

Mantiene solo el texto legal vigente con oraciones completas.
"""

import re
import fitz
from pathlib import Path


def is_footer_line(line: str) -> bool:
    """Detecta lineas de encabezado/pie de pagina."""
    stripped = line.strip()
    footer_markers = [
        "Decreto Ley",
        "Biblioteca del Congreso",
        "www.leychile.cl",
        "documento generado",
        "pagina",
        "página",
    ]
    return any(m.lower() in stripped.lower() for m in footer_markers)


def is_footnote_line(line: str) -> bool:
    """Detecta notas al pie de modificaciones."""
    stripped = line.strip()
    if not stripped:
        return False

    patterns = [
        r'^Ley\s+\d+$',
        r'^LEY\s+\d+$',
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
    """Limpia texto extraido del PDF."""
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


def split_into_sentences(text: str) -> list[str]:
    """Divide texto en oraciones."""
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ])', text)
    return [s.strip() for s in sentences if s.strip()]


def chunk_text(text: str, max_chars: int = 3500) -> list[str]:
    """Divide texto en chunks con oraciones completas."""
    if len(text) <= max_chars:
        return [text]

    sentences = split_into_sentences(text)
    chunks = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) + 1 > max_chars:
            if current:
                chunks.append(current.strip())
            current = sentence
        else:
            current = current + " " + sentence if current else sentence

    if current:
        chunks.append(current.strip())

    return chunks


def extract_article_from_pdf(pdf_path: str, article_num: str) -> str | None:
    """Extrae un articulo completo de un PDF."""
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"
    doc.close()

    # Buscar patron de inicio (varios formatos posibles)
    patterns = [
        rf'ARTICULO\s+{re.escape(article_num)}[°\u00ba]?\.?\s*-',
        rf'ART\.\s+{re.escape(article_num)}[°\u00ba]?\.?\s*-',
        rf'Art\u00edculo\s+{re.escape(article_num)}[°\u00ba]?\.?\s*-',
    ]

    start = -1
    for pat in patterns:
        m = re.search(pat, full_text, re.IGNORECASE)
        if m:
            start = m.start()
            break

    if start < 0:
        return None

    # Buscar siguiente articulo
    end = len(full_text)
    next_pats = [
        r'\n\s*ARTICULO\s+\d+',
        r'\n\s*Art\u00edculo\s+\d+',
    ]
    for next_pat in next_pats:
        m = re.search(next_pat, full_text[start + 30:])
        if m:
            candidate = start + 30 + m.start()
            if candidate < end:
                end = candidate

    raw = full_text[start:end]
    cleaned = clean_text(raw)
    return cleaned if cleaned else None


def extract_article_14_letters(pdf_path: str) -> dict[str, str]:
    """Extrae todas las letras del Art. 14 (A, B, C, D, E)."""
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"
    doc.close()

    idx14 = full_text.find("ARTICULO 14")
    idx15 = full_text.find("ARTICULO 15")
    art14 = full_text[idx14:idx15]

    results = {}
    letters = ["A", "B", "C", "D", "E"]

    for i, letter in enumerate(letters):
        # Buscar la letra al inicio de linea con indentacion
        pattern = rf'\n\s{{2,6}}\(?{letter}\)?\s'
        m = re.search(pattern, art14)
        if not m:
            print(f"  [WARN] Letra {letter} no encontrada en Art. 14")
            continue

        l_start = m.start()

        # Buscar siguiente letra como fin
        next_letter_idx = len(art14)
        for next_l in letters[i + 1:]:
            next_pat = rf'\n\s{{2,6}}\(?{next_l}\)?\s'
            nm = re.search(next_pat, art14[l_start + 10:])
            if nm:
                candidate = l_start + 10 + nm.start()
                if candidate < next_letter_idx:
                    next_letter_idx = candidate
                    break

        # Tambien buscar ARTICULO 14 BIS o ARTICULO 15
        for fin_pat in ["ARTICULO 14 BIS", "ARTICULO 15"]:
            fidx = art14.find(fin_pat, l_start + 20)
            if fidx > 0 and fidx < next_letter_idx:
                next_letter_idx = fidx

        raw = art14[l_start:next_letter_idx]
        cleaned = clean_text(raw)
        if cleaned:
            key = f"14_{letter.lower()}"
            results[key] = cleaned
            print(f"  [OK] Art. 14 {letter}): {len(cleaned)} chars")

    return results


def extract_article_17_n8(pdf_path: str) -> str | None:
    """Extrae la letra b) del Art. 17 N 8 (inmuebles)."""
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"
    doc.close()

    idx17 = full_text.find("ARTICULO 17")
    idx18 = full_text.find("ARTICULO 18")
    art17 = full_text[idx17:idx18]

    idx_8000 = art17.find("8.000 unidades de fomento")
    if idx_8000 < 0:
        return None

    b_start = art17.rfind("b) Enajenaci", 0, idx_8000)
    if b_start < 0:
        return None

    sub = art17[b_start:]
    end_pos = len(sub)
    for m in re.finditer(r'\n\s*c\)', sub[1000:]):
        pos = m.start() + 1000
        ctx = sub[pos - 200:pos]
        if "Ley " in ctx and "D.O." in ctx:
            continue
        end_pos = pos
        break

    raw = sub[:end_pos]
    cleaned = clean_text(raw)
    return cleaned


if __name__ == "__main__":
    pdf = "documents/DL-824_31-DIC-1974.pdf"
    print("[TEST] Art. 14 letras...")
    r14 = extract_article_14_letters(pdf)
    print(f"Letras: {list(r14.keys())}")
    for k, v in r14.items():
        print(f"  {k}: {len(v)} chars")
