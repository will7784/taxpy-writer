"""
Law Loader — carga textos completos de leyes con indices de articulos.

Uso:
    from context_rag.law_loader import law_loader

    # Carga lazy al primer acceso
    lir = law_loader.get("lir")
    # lir.text -> texto completo
    # lir.article(21) -> texto del Art. 21
    # lir.articles -> dict {numero: texto}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

LAWS_DIR = Path(__file__).parent.parent / "knowledge" / "laws"

LAW_CONFIG: dict[str, dict[str, str]] = {
    "lir": {
        "file": "dl824_lir.txt",
        "index": "dl824_lir.json",
        "name": "Ley sobre Impuesto a la Renta (DL-824)",
        "short": "LIR",
    },
    "iva": {
        "file": "dl825_iva.txt",
        "index": "dl825_iva.json",
        "name": "Ley sobre Impuesto a las Ventas y Servicios (DL-825)",
        "short": "Ley de IVA",
    },
    "ct": {
        "file": "dl830_ct.txt",
        "index": "dl830_ct.json",
        "name": "Codigo Tributario (DL-830)",
        "short": "CT",
    },
}


@dataclass
class Law:
    tag: str
    name: str
    short_name: str
    text: str = ""
    _index: dict[str, tuple[int, int]] = field(default_factory=dict)
    _articles_cache: dict[str, str] = field(default_factory=dict)

    def article(self, num: str) -> str | None:
        """Devuelve el texto de un articulo especifico."""
        num = str(num).upper().strip()
        if num in self._articles_cache:
            return self._articles_cache[num]

        if num not in self._index:
            return None

        start, end = self._index[num]
        text = self.text[start:end].strip()
        self._articles_cache[num] = text
        return text

    @property
    def articles(self) -> dict[str, str]:
        """Todos los articulos como {numero: texto}."""
        return {num: self.article(num) or "" for num in self._index}

    @property
    def article_list(self) -> list[str]:
        """Lista ordenada de numeros de articulo (normalizados)."""
        return sorted(self._index.keys(), key=_sort_key)

    @property
    def token_estimate(self) -> int:
        return len(self.text) // 3


def _sort_key(num: str) -> tuple[int, int]:
    """Ordena articulos: 1, 1A, 1BIS, 1TER, 2... Transitorios al final."""
    import re
    num = num.upper()
    if num.startswith("TRANS_"):
        clean = num.replace("TRANS_", "")
        digits = re.sub(r"[^\d]", "", clean)
        return (9999, int(digits) if digits else 0)
    # Extraer solo digitos del inicio para ordenar
    digits = re.match(r"(\d+)", num)
    return (int(digits.group(1)) if digits else 0, 0)


class LawLoader:
    """Carga perezosa de leyes completas en memoria."""

    def __init__(self) -> None:
        self._laws: dict[str, Law] = {}
        self._loaded: bool = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        # La jurisdiccion activa (jurisdictions/) puede sobreescribir leyes y directorio
        law_config, laws_dir = LAW_CONFIG, LAWS_DIR
        try:
            from jurisdictions import get_jurisdiction
            j = get_jurisdiction()
            law_config = j.law_config or LAW_CONFIG
            laws_dir = j.laws_dir or LAWS_DIR
        except Exception:
            pass
        for tag, cfg in law_config.items():
            txt_path = laws_dir / cfg["file"]
            idx_path = laws_dir / cfg["index"]

            if not txt_path.exists():
                print(f"[LawLoader] WARN: {txt_path} no existe, saltando {tag}")
                continue

            text = txt_path.read_text(encoding="utf-8")
            index: dict[str, tuple[int, int]] = {}
            if idx_path.exists():
                raw_idx = json.loads(idx_path.read_text(encoding="utf-8"))
                index = {k: tuple(v) for k, v in raw_idx.items()}

            self._laws[tag] = Law(
                tag=tag,
                name=cfg["name"],
                short_name=cfg["short"],
                text=text,
                _index=index,
            )
        self._loaded = True
        print(f"[LawLoader] {len(self._laws)} leyes cargadas "
              f"({sum(l.token_estimate for l in self._laws.values()):,} tokens est.)")

    def get(self, tag: str) -> Law | None:
        self._ensure_loaded()
        return self._laws.get(tag)

    def get_by_keywords(self, text: str) -> list[Law]:
        """Encuentra leyes relevantes por keywords en la consulta."""
        self._ensure_loaded()
        t = text.lower()
        results: list[Law] = []
        for tag, law in self._laws.items():
            keywords = {
                "lir": ["renta", "lir", "dl-824", "dl 824", "propyme", "pro-pyme",
                         "impuesto a la renta", "global complementario", "primera categoria"],
                "iva": ["iva", "dl-825", "dl 825", "impuesto a las ventas",
                        "debito fiscal", "credito fiscal", "factura"],
                "ct": ["codigo tributario", "ct", "dl-830", "dl 830",
                       "citacion", "liquidacion", "prescripcion", "giro",
                       "infraccion", "sancion", "cobranza", "sii",
                       "facilidades de pago", "convenio de pago"],
            }
            if any(k in t for k in keywords.get(tag, [])):
                results.append(law)
        return results

    def all(self) -> list[Law]:
        self._ensure_loaded()
        return list(self._laws.values())

    def total_tokens(self) -> int:
        self._ensure_loaded()
        return sum(l.token_estimate for l in self._laws.values())


# Singleton
law_loader = LawLoader()
