"""Jurisdiccion: Chile (activa, con corpus completo)."""

from __future__ import annotations

from jurisdictions.base import JurisdictionConfig

CHILE = JurisdictionConfig(
    code="chile",
    name="Chile",
    status="active",
    law_config={
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
    },
    official_domains=[
        "bcn.cl", "sii.cl", "uaf.cl", "tta.cl", "pjud.cl", "scj.cl", "cmfchile.cl",
        "fiscaliadechile.cl", "diariooficial.interior.gob.cl",
    ],
    currency_units={
        "UF": "~$40.000 CLP",
        "UTA": "~$835.000 CLP",
        "UTM": "~$69.583 CLP",
    },
    notes=("Núcleo local: DL-824, DL-825 y DL-830. Biblioteca oficial ampliable: "
           "BCN/LeyChile, SII, UAF, TTA, Poder Judicial, SCJ, CMF y Ministerio Público."),
)
