"""Jurisdiccion: Colombia (stub — arquitectura lista, falta corpus).

Para activar:
  1. Scrapear/descargar el Estatuto Tributario (Decreto 624 de 1989) desde
     SUIN-Juriscol o secretariasenado.gov.co a knowledge/laws/colombia/
     en el mismo formato que Chile: et_colombia.txt + et_colombia.json
     (indice articulo -> [start, end] generable con legal_parser.py
     agregando un DocumentPattern para el ET colombiano).
  2. Completar law_config abajo con los archivos reales.
  3. Opcional: scrapers de jurisprudencia del Consejo de Estado (Seccion
     Cuarta, lo contencioso tributario) y conceptos/doctrina de la DIAN.
  4. JURISDICCION=colombia en .env.

Parser: el ET colombiano usa "Articulo N. <titulo>" con estructura
articulo -> inciso -> numeral, compatible con el parser jerarquico
universal (legal_parser.py) — solo hay que agregar el patron regex del ET.
"""

from __future__ import annotations

from jurisdictions.base import JurisdictionConfig

COLOMBIA = JurisdictionConfig(
    code="colombia",
    name="Colombia",
    status="stub",
    law_config={
        # TODO: completar cuando exista el corpus scrapeado
        # "et": {
        #     "file": "et_colombia.txt",
        #     "index": "et_colombia.json",
        #     "name": "Estatuto Tributario (Decreto 624 de 1989)",
        #     "short": "ET",
        # },
    },
    official_domains=[
        "suin-juriscol.gov.co",
        "secretariasenado.gov.co",
        "consejodeestado.gov.co",
        "dian.gov.co",
    ],
    currency_units={
        "UVT": "~$49.799 COP (2026, verificar vigencia anual DIAN)",
    },
    notes=(
        "Pendiente: corpus ET + jurisprudencia Consejo de Estado (Seccion Cuarta) "
        "+ doctrina DIAN. Fuentes: SUIN-Juriscol, secretariasenado.gov.co/leyes, "
        "consejodeestado.gov.co, dian.gov.co."
    ),
)
