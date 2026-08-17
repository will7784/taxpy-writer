"""
Context Router — clasifica una consulta para decidir que leyes y fuentes cargar.

Estrategia en dos capas:
  1. Keyword matching (rapido, sin coste)
  2. LLM via chat_completion_structured (preciso, usa Gemini Flash para ahorrar)

Devuelve un RouteDecision con las leyes, tipo de consulta y flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RouteDecision:
    law_tags: list[str] = field(default_factory=list)
    needs_live_search: bool = False
    needs_personal_knowledge: bool = False
    query_type: str = "consulta_legal"
    confidence: str = "keyword"
    reasoning: str = ""


# Mapa de keywords → law_tags
_KEYWORD_MAP: dict[str, list[str]] = {
    # LIR / Renta
    "renta": ["lir"],
    "lir": ["lir"],
    "dl-824": ["lir"],
    "dl 824": ["lir"],
    "propyme": ["lir"],
    "pro-pyme": ["lir"],
    "global complementario": ["lir"],
    "primera categoria": ["lir"],
    "segunda categoria": ["lir"],
    "depreciacion": ["lir"],
    "gasto rechazado": ["lir"],
    "gastos rechazados": ["lir"],
    "credito fiscal": ["lir", "iva"],
    "dividendo": ["lir"],
    "retiro": ["lir"],
    "reinversion": ["lir"],
    "termino de giro": ["lir"],
    "enajenacion": ["lir"],
    "bien raiz": ["lir"],
    "bienes raices": ["lir"],
    "inmueble": ["lir"],
    "inmuebles": ["lir"],
    "honorarios": ["lir"],
    "boleta": ["lir"],
    "boletas": ["lir"],
    "empresa": ["lir"],
    "sociedad": ["lir"],

    # IVA
    "iva": ["iva"],
    "dl-825": ["iva"],
    "dl 825": ["iva"],
    "debito fiscal": ["iva"],
    "factura": ["iva"],
    "facturacion": ["iva"],
    "factura electronica": ["iva"],
    "exportacion": ["iva"],
    "exportaciones": ["iva"],
    "importacion": ["iva"],
    "ventas y servicios": ["iva"],
    "retencion": ["lir", "iva"],
    "retenciones": ["lir", "iva"],
    "impuesto al valor agregado": ["iva"],

    # Codigo Tributario / SII
    "codigo tributario": ["ct"],
    "dl-830": ["ct"],
    "dl 830": ["ct"],
    "sii": ["ct"],
    "citacion": ["ct"],
    "liquidacion": ["ct"],
    "prescripcion": ["ct"],
    "giro": ["ct"],
    "infraccion": ["ct"],
    "sancion": ["ct"],
    "multa": ["ct"],
    "cobranza": ["ct"],
    "embargo": ["ct"],
    "facilidades de pago": ["ct"],
    "convenio de pago": ["ct"],
    "reposicion": ["ct"],
    "reclamacion": ["ct"],
    "secreto tributario": ["ct"],
    "fiscalizacion": ["ct"],
    "fiscalizador": ["ct"],
    "notificacion": ["ct"],
    "notificaciones": ["ct"],
    "declaracion": ["lir", "iva"],
    "declaraciones": ["lir", "iva"],

    # SII tramites (live search y browser)
    "estado tributario": ["ct"],
    "carpeta tributaria": ["ct"],
    "situacion tributaria": ["ct"],
}

_LIVE_SEARCH_KEYWORDS = [
    "actualizacion", "ultima", "reciente", "este año", "2025", "2026",
    "nueva ley", "nueva circular", "nuevo oficio", "modificacion",
    "jurisprudencia", "circular", "oficio", "fallo", "sentencia",
    "pronunciamiento",
]


def classify_keywords(query: str) -> RouteDecision:
    """Clasifica la consulta por keywords (sin LLM, gratis)."""
    q = query.lower()
    law_tags: set[str] = set()
    matched: list[str] = []

    for keyword, tags in _KEYWORD_MAP.items():
        if keyword in q:
            law_tags.update(tags)
            matched.append(keyword)

    needs_live = any(k in q for k in _LIVE_SEARCH_KEYWORDS)

    return RouteDecision(
        law_tags=list(law_tags) if law_tags else ["lir", "iva", "ct"],
        needs_live_search=needs_live,
        needs_personal_knowledge=False,
        query_type="consulta_legal",
        confidence="keyword",
        reasoning=f"Keywords: {', '.join(matched[:5])}" if matched else "sin keywords especificas, cargando todas",
    )


async def classify_llm(query: str, llm_client) -> RouteDecision:
    """Clasifica la consulta usando LLM (Gemini Flash o fallback)."""
    from pydantic import BaseModel, Field

    class LawSelection(BaseModel):
        lir: bool = Field(description="Relacionado con Ley de Renta (DL-824, renta, empresas, personas)")
        iva: bool = Field(description="Relacionado con IVA (DL-825, facturacion, debito/credito fiscal)")
        ct: bool = Field(description="Relacionado con Codigo Tributario (DL-830, SII, procedimientos, sanciones)")
        needs_live_search: bool = Field(description="Necesita busqueda en vivo (jurisprudencia reciente, circulares nuevas)")
        query_type: str = Field(description="Tipo: consulta_legal, tramite_sii, calculo, redaccion_peticion, otro")
        reasoning: str = Field(description="Breve explicacion del razonamiento")

    prompt = (
        f"Clasifica esta consulta tributaria chilena:\n\n"
        f'"{query}"\n\n'
        "Determina que cuerpos legales son relevantes y si necesita busqueda en vivo."
    )

    try:
        result = await llm_client.chat_completion_structured(
            schema=LawSelection,
            messages=[
                {"role": "system", "content": "Eres un clasificador de consultas tributarias chilenas. "
                 "DL-824 = Ley de Renta (LIR). DL-825 = Ley de IVA. DL-830 = Codigo Tributario (CT). "
                 "Responde solo con el JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=300,
        )
    except Exception:
        return classify_keywords(query)

    law_tags: list[str] = []
    if result.lir:
        law_tags.append("lir")
    if result.iva:
        law_tags.append("iva")
    if result.ct:
        law_tags.append("ct")
    if not law_tags:
        law_tags = ["lir", "iva", "ct"]

    return RouteDecision(
        law_tags=law_tags,
        needs_live_search=result.needs_live_search,
        needs_personal_knowledge=False,
        query_type=result.query_type,
        confidence="llm",
        reasoning=result.reasoning,
    )


async def route_query(query: str, llm_client=None) -> RouteDecision:
    """Router principal: keyword como fallback, LLM si esta disponible."""
    if llm_client is None:
        return classify_keywords(query)
    try:
        return await classify_llm(query, llm_client)
    except Exception:
        return classify_keywords(query)
