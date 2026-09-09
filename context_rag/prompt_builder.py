"""
Prompt Builder — ensambla el prompt para el LLM con leyes y fuentes.

Estrategia de contexto:
  1. Si la ley cabe completa → se incluye entera
  2. Si no cabe → smart trim: selecciona articulos completos mas relevantes
     + indice del resto (NUNCA corta a mitad de articulo)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

import config
from context_rag.context_router import RouteDecision, route_query
from context_rag.law_loader import Law, law_loader
from context_rag.law_map import law_map
from litm import maybe_reorder

console = Console()

AGENT_MD_PATH = Path(__file__).parent.parent / "agent.md"
SKILLS_ENABLED = True  # Fase 3: skills dinamicas

# Margen de seguridad para el system prompt + user query + headers
PROMPT_OVERHEAD_TOKENS = 30_000

# Marcador de la seccion de leyes dentro del user prompt
LAW_SECTION_HEADER = "=== FUENTES LEGALES ===\n"


@dataclass
class PromptInput:
    query: str
    route: RouteDecision = field(default_factory=RouteDecision)
    personal_docs: list[str] = field(default_factory=list)
    live_context: str = ""
    trim_applied: bool = False
    articles_included: int = 0
    articles_total: int = 0
    tokens_used: int = 0


def _load_agent_md() -> str:
    if AGENT_MD_PATH.exists():
        return AGENT_MD_PATH.read_text(encoding="utf-8")
    return ""


def _format_law_header(law: Law, partial: bool = False) -> str:
    status = "PARCIAL (smart trim aplicado)" if partial else "TEXTO COMPLETO"
    return (
        f"─────────────────────────────────────────────────────────────\n"
        f"{status}: {law.name}\n"
        f"({law.short_name}, {len(law._index)} articulos, ~{law.token_estimate:,} tokens)\n"
        f"─────────────────────────────────────────────────────────────\n\n"
    )


def _build_system_prompt(base_agent_md: str | None = None, query: str = "") -> str:
    agent_md = base_agent_md or _load_agent_md()

    system = (
        f"{agent_md}\n\n"
        "─── INSTRUCCIONES ADICIONALES PARA ESTE MODO ───\n\n"
        "Tienes acceso al texto de las leyes indicadas mas abajo.\n"
        'Si ves "TEXTO COMPLETO" tienes la ley entera.\n'
        'Si ves "PARCIAL" tienes los articulos mas relevantes + un indice del resto.\n\n'
        "Cada ley se abre con un INDICE ESTRUCTURAL (LIBRO / TITULO / PARRAFO con el "
        "rango de articulos de cada seccion). Usa ese indice para UBICAR el tema dentro "
        "de la ley antes de leer su texto: asi sabes exactamente que articulos leer y "
        "no dices que una norma no existe solo porque no la viste.\n\n"
        "REGLAS:\n"
        "1. Toda cita DEBE venir del texto legal proporcionado. Si no aparece, di "
        "'No encontre esa informacion en el texto de la ley que tengo disponible. "
        "Prueba preguntando por un articulo o tema mas especifico.'\n"
        "2. El numero de articulo, inciso y numeral debe ser EXACTO.\n"
        "3. NO inventes articulos ni uses conocimiento externo.\n"
        "4. Cuando cites, usa el formato: '(Art. XX, [Nombre de la Ley])'.\n"
        "5. Un documento marcado como proyecto_en_tramitacion NO es derecho vigente: "
        "identificalo como proyecto y no atribuyas efectos actuales.\n"
        "6. Responde en tono conversacional, sin markdown, max 250 palabras."
    )

    if SKILLS_ENABLED and query:
        try:
            from skill_manager import load_skills_context
            skills_text = load_skills_context(query)
            if skills_text:
                system += "\n" + skills_text
        except Exception:
            pass

    return system


# ── Articulos criticos forzados por tema ───────────────────────
# Estos siempre se incluyen (max score) si el query matchea el tema.
# Evita que el keyword scoring omita articulos clave.
_DOMAIN_CRITICAL: dict[str, dict[str, list[str]]] = {
    "lir": {
        "propyme": ["14", "14 D", "14 E"],
        "regimen": ["14", "14 D", "14 E"],
        "pyme": ["14", "14 D", "14 E"],
        "transparencia": ["14 D"],
        "simplificado": ["14 E"],
        "depreciacion": ["31"],
        "depreciar": ["31"],
        "gasto rechazado": ["21"],
        "gastos rechazados": ["21"],
        "credito": ["33 BIS"],
        "inmueble": ["17"],
        "inmuebles": ["17"],
        "bien raiz": ["17"],
        "bienes raices": ["17"],
        "enajenacion": ["17"],
        "dividendo": ["17"],
        "dividendos": ["17"],
        "retiro": ["14"],
        "termino de giro": ["38 BIS"],
        "honorarios": ["42"],
        "boleta": ["42"],
        "boletas": ["42"],
        "renta presunta": ["20"],
        "global complementario": ["52"],
        "primera categoria": ["20"],
        "correccion monetaria": ["41"],
        "retiro para reinvertir": ["14"],
    },
    "iva": {
        "iva": ["8", "11", "12", "23", "24", "25", "26", "27", "28"],
        "debito fiscal": ["20", "21", "22", "23"],
        "credito fiscal": ["23", "24", "25", "26", "27", "28"],
        "factura": ["52", "53", "54", "55"],
        "facturacion": ["52", "53", "54", "55"],
        "exportacion": ["36"],
        "exportaciones": ["36"],
        "importacion": ["8", "9"],
        "importaciones": ["8", "9"],
        "retencion": ["64", "74"],
        "sujeto": ["8"],
        "hecho gravado": ["8"],
        "exento": ["12", "13"],
        "exencion": ["12", "13"],
    },
    "ct": {
        "citacion": ["63"],
        "liquidacion": ["24", "64", "65"],
        "giro": ["24", "37"],
        "prescripcion": ["200", "201"],
        "infraccion": ["97", "98", "99", "100", "101", "102", "103", "104", "105", "106", "107", "108", "109"],
        "sancion": ["97", "98"],
        "multa": ["97", "98"],
        "cobranza": ["172", "173", "174", "175", "176", "177"],
        "embargo": ["172", "173", "174", "175", "176", "177"],
        "facilidades de pago": ["192"],
        "convenio de pago": ["192"],
        "reposicion": ["120", "121", "122", "123"],
        "reclamacion": ["120", "121", "122", "123", "124", "125"],
        "secreto tributario": ["35", "36", "37"],
        "fiscalizacion": ["59", "60", "61", "62", "63"],
        "fiscalizador": ["59", "60", "61", "62", "63"],
        "notificacion": ["11", "12", "13", "14", "15"],
        "notificaciones": ["11", "12", "13", "14", "15"],
        "declaracion": ["29", "30", "31", "32", "33", "34", "35", "36", "37", "38", "39", "40"],
        "sii": ["1", "6", "59", "60", "61", "62", "63"],
        "intereses": ["53", "54"],
        "reajustes": ["53", "54"],
        "mora": ["53", "54"],
        "condonacion": ["56", "192"],
    },
}


def _get_forced_articles(query: str, law_tag: str) -> set[str]:
    """Devuelve los numeros de articulo que DEBEN incluirse para este tema."""
    q = query.lower()
    forced: set[str] = set()
    domain = _DOMAIN_CRITICAL.get(law_tag, {})
    for keyword, articles in domain.items():
        if keyword in q:
            forced.update(articles)
    return forced


_SCORE_STOPWORDS: set[str] = {
    "que", "el", "la", "los", "las", "de", "del", "en", "un", "una",
    "es", "y", "o", "a", "para", "por", "con", "se", "no", "si",
    "su", "al", "como", "lo", "le", "me", "mi", "tu", "te", "ha",
}


def _relevance_summary(law: Law, query: str, top_n: int = 8) -> str:
    """Resumen compacto de los articulos mas relevantes para la consulta.

    Se coloca al inicio de cada ley para dar primacia a la informacion clave
    y mitigar el efecto Lost-in-the-Middle en leyes largas cargadas completas.
    """
    forced = _get_forced_articles(query, law.tag)
    q_words = {w for w in query.lower().split() if w and w not in _SCORE_STOPWORDS}

    relevant: list[str] = list(forced)
    keyword_hits: list[tuple[int, str]] = []
    for num in law.article_list:
        if num in relevant:
            continue
        text = (law.article(num) or "").lower()
        hits = sum(1 for w in q_words if w in text)
        if hits:
            keyword_hits.append((hits, num))
    keyword_hits.sort(key=lambda x: x[0], reverse=True)
    relevant.extend(num for _h, num in keyword_hits)

    if not relevant:
        return ""

    lines = ["--- ARTICULOS MAS RELEVANTES PARA ESTA CONSULTA ---"]
    for num in relevant[:top_n]:
        desc = (law.article(num) or "")[:100].replace("\n", " ").strip()
        lines.append(f"  • Art. {num}: {desc}...")
    return "\n".join(lines) + "\n\n"


def _score_articles(query: str, law: Law) -> list[tuple[str, float]]:
    """Puntua cada articulo por relevancia de keywords contra la query.
    Los articulos forzados por dominio reciben score maximo (999)."""
    q_words = set(query.lower().split())
    q_words.discard("")
    q_words -= _SCORE_STOPWORDS

    forced = _get_forced_articles(query, law.tag)

    if not q_words and not forced:
        return [(num, 1.0 - i * 0.001) for i, num in enumerate(law.article_list)]

    scored: list[tuple[str, float]] = []
    for num in law.article_list:
        # Forzados por dominio: score maximo
        if num in forced or any(num == f or num.startswith(f"{f}_") or f.startswith(f"{num}_") for f in forced):
            scored.append((num, 999.0))
            continue

        text = (law.article(num) or "").lower()
        if not text:
            continue
        hits = sum(1 for w in q_words if w in text)
        density = hits / max(len(text) / 1000, 1)
        bonus = 0.5 if any(w == num.lower() or w == num.lower().lstrip("0") for w in q_words) else 0
        scored.append((num, density + bonus))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _build_article_index(law: Law, excluded: set[str]) -> str:
    """Construye un indice compacto de articulos NO incluidos en el texto."""
    lines = ["\n--- INDICE DE ARTICULOS NO INCLUIDOS ---\n"]
    lines.append("(Si necesitas uno de estos articulos, preguntamelo especificamente)\n")
    current_ten = None
    for num in law.article_list:
        if num in excluded:
            continue
        # Extraer primeras palabras como descripcion
        text = law.article(num) or ""
        desc = text[:120].replace("\n", " ").strip()
        # Agrupar por decenas
        digits = re.match(r"(\d+)", num)
        ten = int(digits.group(1)) // 10 * 10 if digits else 0
        if ten != current_ten:
            current_ten = ten
            lines.append(f"\n  Arts. {ten}-{ten + 9}:")
        lines.append(f"    Art. {num}: {desc}...")
    return "\n".join(lines)


def _smart_trim_law(law: Law, query: str, max_tokens: int) -> tuple[str, int, bool]:
    """Selecciona los articulos mas relevantes que caben en max_tokens.

    Returns:
        (texto, tokens_usados, fue_trimeado)
    """
    full_tokens = law.token_estimate
    summary = _relevance_summary(law, query)
    structure_map = law_map.to_text_for([law.tag]) if law_map.build_for_tag(law.tag) else ""

    if full_tokens <= max_tokens:
        text = _format_law_header(law) + structure_map + summary + law.text
        return text, full_tokens, False

    # ── Smart trim: seleccionar articulos completos ──────────────
    scored = _score_articles(query, law)
    included: list[str] = []
    included_set: set[str] = set()
    tokens_used = 0

    for num, score in scored:
        if score <= 0:
            continue
        art = law.article(num)
        if not art:
            continue
        art_tokens = len(art) // 3 + 50  # +50 por header del articulo
        if tokens_used + art_tokens > max_tokens:
            continue
        included.append(f"[Art. {num}]\n{art}\n")
        included_set.add(num)
        tokens_used += art_tokens

    # Reordenar articulos en forma de U para que los mas relevantes queden
    # al inicio y al final, mitigando el efecto Lost-in-the-Middle.
    included = maybe_reorder(included)

    # Agregar indice del resto
    if len(included_set) < len(law._index):
        index_text = _build_article_index(law, included_set)
        index_tokens = len(index_text) // 3
        if tokens_used + index_tokens <= max_tokens:
            included.append(index_text)
            tokens_used += index_tokens

    header = _format_law_header(law, partial=True)
    text = header + structure_map + summary + "\n".join(included)
    return text, tokens_used, True


def build_context(
    query: str,
    route: RouteDecision | None = None,
    personal_docs: list[str] | None = None,
    live_context: str = "",
    llm_client=None,
) -> tuple[str, str, PromptInput]:
    """Construye system_prompt y user_prompt para el LLM.

    Usa el max_context del LLM si esta disponible; si no, asume 900K (Gemini).
    """
    personalized = personal_docs or []

    # Determinar ventana de contexto disponible
    if llm_client and hasattr(llm_client, "max_context"):
        max_context = llm_client.max_context
    else:
        max_context = 900_000  # default: Gemini

    available_tokens = max_context - PROMPT_OVERHEAD_TOKENS

    system = _build_system_prompt(query=query)

    # ── Seleccion y carga de leyes ──────────────────────────────
    if route:
        tags = route.law_tags
    else:
        tags = ["lir", "iva", "ct"]

    # Distribuir tokens disponibles entre las leyes seleccionadas
    tokens_per_law = max(available_tokens // len(tags), 20_000)
    system_tokens = len(system) // 3

    law_texts: list[str] = []
    loaded_laws: list[Law] = []
    total_used = system_tokens
    trim_applied = False
    total_arts_included = 0
    total_arts = 0

    for tag in tags:
        law = law_loader.get(tag)
        if not law:
            continue
        remaining = max_context - total_used - PROMPT_OVERHEAD_TOKENS
        if remaining <= 10_000:
            break

        # Reservar presupuesto equitativo: antes el primer cuerpo legal podia
        # consumir toda la ventana y excluir los restantes.
        text, used, trimmed = _smart_trim_law(law, query, min(remaining, tokens_per_law))
        law_texts.append(text)
        loaded_laws.append(law)
        total_used += used
        if trimmed:
            trim_applied = True
        total_arts += len(law._index)

    if not loaded_laws:
        for tag in ["iva", "lir", "ct"]:
            law = law_loader.get(tag)
            if law:
                remaining = max_context - total_used - PROMPT_OVERHEAD_TOKENS
                text, used, trimmed = _smart_trim_law(law, query, remaining)
                law_texts.append(text)
                loaded_laws.append(law)
                break

    # ── Construir user prompt ──────────────────────────────────
    parts: list[str] = []
    parts.append("=== FUENTES LEGALES ===\n")
    parts.extend(law_texts)

    if personalized:
        personalized = maybe_reorder(personalized)
        parts.append("\n=== DOCUMENTOS PERSONALES DEL USUARIO ===\n")
        for i, doc in enumerate(personalized, 1):
            parts.append(f"\n[Documento {i}]\n{doc}\n")

    if live_context.strip():
        parts.append("\n=== FUENTES EN VIVO (WEB) ===\n")
        parts.append(live_context)

    parts.append("=== CONSULTA DEL USUARIO ===\n")
    parts.append(query)
    parts.append(
        "\n\n=== RECORDATORIO FINAL (aplica antes de responder) ===\n"
        "1. Usa SOLO el texto legal y las fuentes proporcionadas arriba.\n"
        "2. Los articulos mas relevantes estan destacados al inicio de cada ley.\n"
        "3. Cita el numero de articulo EXACTO y la ley correspondiente.\n"
        "4. Si la respuesta no esta en las fuentes, dilo honestamente y sugiere "
        "preguntar por un articulo o tema mas especifico."
    )

    user_prompt = "\n".join(parts)

    prompt_input = PromptInput(
        query=query,
        route=route or RouteDecision(law_tags=[law.tag for law in loaded_laws]),
        personal_docs=personalized,
        live_context=live_context,
        trim_applied=trim_applied,
        articles_included=total_arts_included,
        articles_total=total_arts,
        tokens_used=total_used,
    )

    return system, user_prompt, prompt_input


def _split_law_section(user_prompt: str) -> tuple[str, str]:
    """Separa el bloque de leyes del resto del prompt.

    Returns:
        (bloque_leyes, resto) donde `resto` conserva docs, fuentes en vivo,
        consulta y recordatorio final.
    """
    idx = user_prompt.find(LAW_SECTION_HEADER)
    if idx < 0:
        return "", user_prompt
    body = user_prompt[idx + len(LAW_SECTION_HEADER):]
    for marker in (
        "\n=== DOCUMENTOS PERSONALES",
        "\n=== FUENTES EN VIVO",
        "\n=== CONSULTA DEL USUARIO",
    ):
        m = body.find(marker)
        if m >= 0:
            return body[:m], body[m:]
    return body, ""


async def _summarize_law(law: Law, query: str, llm_client) -> str:
    """Paso intermedio (pre-resumir): destila una ley a un resumen denso y fiel.

    Conserva numero de articulo y cifras exactas, mitigando Lost-in-the-Middle
    al reducir el volumen de texto que el modelo final debe atender.
    """
    max_input = getattr(llm_client, "max_context", 128_000) - PROMPT_OVERHEAD_TOKENS
    law_text, _, _ = _smart_trim_law(law, query, max_input)

    system = (
        "Eres un compilador de normativa tributaria chilena. Recibes el texto de una ley "
        "y debes producir un RESUMEN DENSO y FIEL orientado a la consulta del usuario.\n"
        "REGLAS OBLIGATORIAS:\n"
        "1. Conserva el NUMERO DE ARTICULO junto a cada regla (ej: 'Art. 21: ...').\n"
        "2. Conserva TODAS las cifras exactas: montos en UF/UTM/UTA, porcentajes, plazos, tasas, topes.\n"
        "3. Conserva requisitos, exenciones, sanciones y condiciones.\n"
        "4. NO inventes articulos, cifras ni requisitos que no esten en el texto.\n"
        "5. Si el texto no contiene un dato, NO lo agregues.\n"
        "6. Formato conciso, lista por articulo. Sin titulos markdown grandes."
    )
    user = (
        f"Consulta del usuario: {query}\n\n"
        f"Texto de la ley ({law.short_name}):\n\n{law_text}\n\n"
        "Produce el resumen denso siguiendo las reglas."
    )
    try:
        summary = await llm_client.chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=6000,
        )
        return summary.strip()
    except Exception as e:
        console.print(f"[yellow][PRE-SUM] Fallo resumiendo {law.tag}: {e}[/yellow]")
        return ""


async def maybe_pre_summarize(
    user_prompt: str,
    query: str,
    route: RouteDecision | None,
    llm_client,
    prompt_input: PromptInput,
) -> str:
    """Aplica pre-resumir (compresion con LLM intermedio) si el contexto es muy extenso.

    Solo interviene cuando las leyes se cargaron completas (sin smart trim), porque
    ahi es donde el efecto Lost-in-the-Middle es mas agudo.
    """
    if llm_client is None or not getattr(config, "PRE_SUMMARIZE", True):
        return user_prompt
    if prompt_input.trim_applied:
        return user_prompt
    tokens_est = prompt_input.tokens_used or (len(user_prompt) // 3)
    if tokens_est < getattr(config, "PRE_SUMMARIZE_MIN_TOKENS", 60_000):
        return user_prompt

    _laws, tail = _split_law_section(user_prompt)
    if not tail:
        return user_prompt

    tags = route.law_tags if route else ["lir", "iva", "ct"]
    summaries: list[str] = []
    for tag in tags:
        law = law_loader.get(tag)
        if not law:
            continue
        summary = await _summarize_law(law, query, llm_client)
        if not summary:
            continue
        summaries.append(
            f"─────────────────────────────────────────────────────────────\n"
            f"RESUMEN DESTILADO: {law.name} ({law.short_name})\n"
            f"─────────────────────────────────────────────────────────────\n\n"
            f"{summary}"
        )

    if not summaries:
        return user_prompt

    return LAW_SECTION_HEADER + "\n\n".join(summaries) + "\n\n" + tail


async def build_for_chat(
    query: str,
    llm_client=None,
    personal_docs: list[str] | None = None,
    live_context: str = "",
) -> tuple[str, str, PromptInput]:
    """Flujo completo: routea la consulta y construye el prompt."""
    route = await route_query(query, llm_client)
    system, user_prompt, prompt_input = build_context(
        query=query,
        route=route,
        personal_docs=personal_docs,
        live_context=live_context,
        llm_client=llm_client,
    )
    user_prompt = await maybe_pre_summarize(
        user_prompt=user_prompt,
        query=query,
        route=route,
        llm_client=llm_client,
        prompt_input=prompt_input,
    )
    return system, user_prompt, prompt_input
