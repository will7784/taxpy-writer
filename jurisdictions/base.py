"""
Configuracion por jurisdiccion — permite replicar el agente a otros paises.

Cada jurisdiccion define:
  - code / name
  - law_config: cuerpos legales indexados (mismo formato que law_loader.LAW_CONFIG)
  - laws_dir: directorio con los .txt/.json de leyes (knowledge/laws/<code>)
  - official_domains: dominios oficiales para busqueda en vivo (live_lookup)
  - currency_units: unidades tributarias locales (para instrucciones de CLP/COP)
  - status: "active" (con corpus) | "stub" (arquitectura lista, falta corpus)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import config


@dataclass
class JurisdictionConfig:
    code: str
    name: str
    status: str = "stub"
    law_config: dict[str, dict[str, str]] = field(default_factory=dict)
    laws_dir: Path | None = None
    official_domains: list[str] = field(default_factory=list)
    currency_units: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self) -> None:
        if self.laws_dir is None:
            # knowledge/laws/<code> si existe; si no, knowledge/laws (Chile legacy)
            per_country = config.KNOWLEDGE_DIR / "laws" / self.code
            legacy = config.KNOWLEDGE_DIR / "laws"
            self.laws_dir = per_country if per_country.exists() else legacy
