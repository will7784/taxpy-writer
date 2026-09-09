"""Catálogo mínimo y verificable de la biblioteca jurídica chilena.

El catálogo es una lista de puntos de entrada oficiales, no una declaración de
que todo el portal ya fue descargado. ``ProductionStore`` registra para cada
entrada si se descubrió, descargó, leyó e indexó correctamente.
"""
from __future__ import annotations

from typing import Any


OFFICIAL_LIBRARY_CATALOG: tuple[dict[str, Any], ...] = (
    {"key": "lir", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Ley sobre Impuesto a la Renta (DL 824)",
     "url": "https://www.bcn.cl/leychile/navegar?idNorma=6368", "collection": "legislacion_base"},
    {"key": "iva", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Ley sobre Impuesto a las Ventas y Servicios (DL 825)",
     "url": "https://www.bcn.cl/leychile/navegar?idNorma=6379", "collection": "legislacion_base"},
    {"key": "ct", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Código Tributario (DL 830)",
     "url": "https://www.bcn.cl/leychile/navegar?idNorma=6374", "collection": "legislacion_base"},
    {"key": "codigo_civil", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Código Civil", "url": "https://www.bcn.cl/leychile/navegar?idNorma=172986", "collection": "derecho_comun"},
    {"key": "codigo_comercio", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Código de Comercio", "url": "https://www.bcn.cl/leychile/navegar?idNorma=1974", "collection": "derecho_comun"},
    {"key": "codigo_penal", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Código Penal", "url": "https://www.bcn.cl/leychile/navegar?idNorma=1984", "collection": "delitos_economicos"},
    {"key": "codigo_procesal_penal", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Código Procesal Penal", "url": "https://www.bcn.cl/leychile/navegar?idNorma=176595", "collection": "delitos_economicos"},
    {"key": "codigo_procedimiento_civil", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Código de Procedimiento Civil", "url": "https://www.bcn.cl/leychile/navegar?idNorma=22740", "collection": "derecho_comun"},
    {"key": "constitucion", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Constitución Política de la República de Chile", "url": "https://www.bcn.cl/leychile/navegar?idNorma=242302", "collection": "legislacion_base"},
    {"key": "ley_19913", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Ley 19.913: Unidad de Análisis Financiero y lavado de activos",
     "url": "https://www.bcn.cl/leychile/navegar?idNorma=219119", "collection": "lavado_activos"},
    {"key": "ley_20393", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Ley 20.393: responsabilidad penal de las personas jurídicas",
     "url": "https://www.bcn.cl/leychile/navegar?idNorma=1008668", "collection": "delitos_economicos"},
    {"key": "ley_21595", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Ley 21.595: delitos económicos", "url": "https://www.bcn.cl/leychile/navegar?idNorma=1195119", "collection": "delitos_economicos"},
    {"key": "ley_19995", "source": "BCN/LeyChile", "document_type": "ley",
     "title": "Ley 19.995: bases generales para la autorización y funcionamiento de casinos",
     "url": "https://www.bcn.cl/leychile/navegar?idNorma=207436", "collection": "casinos"},
    {"key": "sii_normativa", "source": "SII", "document_type": "indice_normativo",
     "title": "Normativa SII: circulares y resoluciones", "url": "https://www.sii.cl/pagina/jurisprudencia/normativa.htm", "collection": "sii"},
    {"key": "sii_admin", "source": "SII", "document_type": "indice_jurisprudencia_administrativa",
     "title": "Jurisprudencia administrativa SII", "url": "https://www.sii.cl/pagina/jurisprudencia/adminis/indice.htm", "collection": "sii"},
    {"key": "uaf_normativa", "source": "UAF", "document_type": "indice_normativo",
     "title": "UAF: ley, delitos base y normativa", "url": "https://www.uaf.cl/es-cl/normativa/nuestra-ley", "collection": "uaf"},
    {"key": "uaf_circulares", "source": "UAF", "document_type": "indice_circulares",
     "title": "Circulares UAF", "url": "https://www.uaf.cl/es-cl/normativa/circulares-uaf", "collection": "uaf"},
    {"key": "uaf_sanciones", "source": "UAF", "document_type": "sanciones_administrativas",
     "title": "Sanciones ejecutoriadas UAF", "url": "https://www.uaf.cl/es-cl/publicaciones-uaf/sanciones-ejecutoriadas", "collection": "uaf"},
    {"key": "uaf_tipologias", "source": "UAF", "document_type": "tipologias",
     "title": "Informes de tipologías y señales de alerta UAF", "url": "https://www.uaf.cl/es-cl/publicaciones-uaf/informe-de-tipologias", "collection": "uaf"},
    {"key": "tta", "source": "TTA", "document_type": "indice_jurisprudencia",
     "title": "Jurisprudencia y sentencias definitivas TTA", "url": "https://www.tta.cl/", "collection": "jurisprudencia"},
    {"key": "pjud", "source": "Poder Judicial", "document_type": "indice_jurisprudencia",
     "title": "Buscador Unificado de Fallos del Poder Judicial", "url": "https://juris.pjud.cl/busqueda", "collection": "jurisprudencia"},
    {"key": "scj", "source": "Superintendencia de Casinos de Juego", "document_type": "indice_oficial",
     "title": "Superintendencia de Casinos de Juego", "url": "https://www.scj.cl/", "collection": "casinos"},
)


def catalog_entries() -> list[dict[str, Any]]:
    return [dict(entry) for entry in OFFICIAL_LIBRARY_CATALOG]
