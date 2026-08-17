"""
Registro de jurisdicciones — punto de entrada unico.

Uso:
    from jurisdictions import get_jurisdiction
    j = get_jurisdiction()            # la activa (config.JURISDICCION)
    j = get_jurisdiction("colombia")  # una especifica
"""

from __future__ import annotations

import config
from jurisdictions.base import JurisdictionConfig
from jurisdictions.chile import CHILE
from jurisdictions.colombia import COLOMBIA

_REGISTRY: dict[str, JurisdictionConfig] = {
    "chile": CHILE,
    "colombia": COLOMBIA,
}


def get_jurisdiction(code: str | None = None) -> JurisdictionConfig:
    code = (code or getattr(config, "JURISDICCION", "chile")).lower()
    return _REGISTRY.get(code, CHILE)


def list_jurisdictions() -> list[JurisdictionConfig]:
    return list(_REGISTRY.values())


__all__ = ["JurisdictionConfig", "get_jurisdiction", "list_jurisdictions"]
