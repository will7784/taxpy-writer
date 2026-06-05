"""
Motor de búsqueda semántica (RAG) usando Supabase pgvector.

Reemplaza a NotebookLM como backend de recuperación de información.
"""

from __future__ import annotations

import re
from typing import Any

from openai import AsyncOpenAI
from rich.console import Console

import config
from graph_engine import graph as graph_engine
from models import DocumentChunk, SearchResult
from supabase_client import supabase

console = Console()


class RAGEngine:
    """
    Motor de Recuperación Augmentada por Generación (RAG).

    Flujo:
    1. Recibe una pregunta del usuario
    2. Genera embedding de la pregunta
    3. Busca los chunks más similares en Supabase pgvector
    4. Fallback por keywords en leyes (caché local) para capturar conceptos
       legales específicos que el embedding semántico puede perder por
       diferencia de estilo (normativo vs. conversacional).
    5. Retorna contexto estructurado para el LLM
    """

    DEFAULT_TOP_K = 10
    CONVERSATION_TOP_K = 20  # Aumentado drásticamente: el LLM necesita ver más contexto
                               # para cruzar leyes (ej: Art. 14 D LIR ↔ Art. 192 CT)

    # Palabras vacías en español + conversacionales que no aportan a búsqueda legal
    _STOPWORDS: set[str] = {
        "qué", "cómo", "cuál", "dónde", "cuándo", "por", "para", "con", "sin", "sobre",
        "bajo", "entre", "desde", "hasta", "después", "antes", "durante", "mediante",
        "según", "como", "más", "menos", "muy", "tan", "tanto", "cada", "todo", "toda",
        "todos", "todas", "algún", "alguna", "algunos", "algunas", "ningún", "ninguna",
        "otro", "otra", "otros", "otras", "mismo", "misma", "mismos", "mismas", "tal",
        "tales", "cual", "cuales", "que", "el", "la", "los", "las", "un", "una", "unos",
        "unas", "del", "al", "de", "a", "en", "es", "son", "fue", "ser", "estar", "tener",
        "haber", "hacer", "poder", "deber", "querer", "saber", "decir", "ver", "dar", "ir",
        "venir", "poner", "salir", "pasar", "pensar", "seguir", "volver", "parecer", "quedar",
        "llamar", "llegar", "creer", "dejar", "mirar", "escuchar", "tomar", "trabajar", "usar",
        "empezar", "terminar", "ayudar", "mostrar", "importar", "explicar", "concepto", "sobre",
        "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas", "aquel", "aquella",
        "aquellos", "aquellas", "yo", "tú", "él", "ella", "nosotros", "nosotras", "vosotros",
        "vosotras", "ellos", "ellas", "me", "te", "se", "nos", "os", "lo", "le", "les",
        "mi", "tu", "su", "nuestro", "vuestra", "suyo", "mío", "tuyo", "cuando", "donde",
        "quien", "cuyo", "cuya", "cuyos", "cuyas", "cuanto", "cuanta", "cuantos", "cuantas",
        "asi", "tambien", "pero", "sino", "aunque", "porque", "pues", "ya", "aun", "solo",
        "solamente", "bien", "mal", "ahora", "entonces", "luego", "siempre", "nunca",
        "jamás", "quizás", "talvez", "acaso", "verdaderamente", "realmente", "ciertamente",
        "efectivamente", "exactamente", "precisamente", "particularmente", "especialmente",
        "generalmente", "normalmente", "frecuentemente", "constantemente", "continuamente",
        "inmediatamente", "directamente", "indirectamente", "claramente", "evidentemente",
        "obviamente", "aparentemente", "supuestamente", "presumiblemente", "probablemente",
        "posiblemente", "quizá", "vez", "sea", "sean", "fuese", "hubiese", "tuviese",
        "pudiese", "debiese", "diga", "haga", "vaya", "esté", "estén", "and", "the", "or",
        "what", "how", "when", "where", "why", "which", "who", "whom", "whose", "this",
        "that", "these", "those", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "shall", "should",
        "may", "might", "can", "could", "must", "ought", "need", "dare", "used", "to",
        "of", "for", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "up", "down", "out", "off", "over", "under",
        "again", "further", "then", "once", "here", "there", "all", "any", "both", "each",
        "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own",
        "same", "so", "than", "too", "very", "just", "but", "if", "while", "because",
        "until", "since", "although", "unless", "whether", "either", "neither", "also",
        "really", "actually", "definitely", "certainly", "probably", "possibly", "perhaps",
        "maybe", "simply", "basically", "essentially", "fundamentally", "primarily",
        "mainly", "mostly", "largely", "partly", "fully", "completely", "totally",
        "absolutely", "relatively", "fairly", "quite", "rather", "pretty", "enough",
        "almost", "nearly", "hardly", "barely", "scarcely", "seldom", "rarely",
        "frequently", "often", "sometimes", "usually", "typically", "commonly",
        "regularly", "repeatedly", "consistently", "occasionally", "periodically",
        "temporarily", "permanently", "explícame", "explique", "dime", "cuéntame",
        "cuentame", "indícame", "indicame", "explíqueme", "diga", "dígame", "háblame",
        "hablame", "muéstrame", "muestrame", "enséñame", "enseñame", "explicame",
        "quiero", "quisiera", "necesito", "gustaría", "gustaria", "podrías", "podrias",
        "podría", "podria", "puedes", "puede", "pueden", "puedo", "pueda", "pudiera",
        "podrían", "podrian", "sería", "seria", "serian", "serían", "estaría", "estaria",
        "estuviera", "estuviese", "hubiera", "hubiese", "tuviera", "tuviese", "tendría",
        "tendria", "tendrían", "tendrian", "haría", "haria", "harían", "harian", "diría",
        "diria", "dirían", "dirian", "vería", "veria", "verían", "verian", "sabría",
        "sabria", "sabrían", "sabrian", "dónde", "cuál", "cuáles", "quién", "quiénes",
    }

    # ── Sinónimos legales: expande keywords del usuario a términos del texto legal ──
    # Cada clave: palabra(s) que el usuario puede usar.  Valor: lista de términos legales
    # equivalentes que aparecen en los documentos.
    _SYNONYMS: dict[str, list[str]] = {
        # PYME = forma correcta.  PIME / PROPIME = pronunciación/escritura errónea común en audio.
        # El retrieval debe buscar PYME cuando el usuario dice PIME.
        "pro": ["pro"],
        "propyme": ["pro", "propyme", "propime"],
        "propymé": ["propyme", "pyme", "pro"],
        "pime": ["propyme", "pyme", "pro"],
        "pyme": ["pyme", "propyme", "pro"],
        "propime": ["propyme", "pyme", "pro"],
        "propia": ["propyme", "pyme", "pro"],
        "propie": ["propyme", "pyme", "pro"],
        "renta": ["renta", "imponible"],
        "rentas": ["renta", "imponible"],
        "imponible": ["imponible", "renta"],
        "imputación": ["imputación", "imputar", "imputan"],
        "imputar": ["imputación", "imputar", "imputan"],
        "imputan": ["imputación", "imputar", "imputan"],
        "renta_presunta": ["renta_presunta"],
        "renta_debito": ["renta_presunta", "debito"],
        "debito": ["debito"],
        "retencion": ["retencion", "retención", "retener"],
        "retención": ["retencion", "retención", "retener"],
        "retener": ["retencion", "retención", "retener"],
        "perdida": ["perdida", "pérdida", "pérdidas"],
        "pérdida": ["perdida", "pérdida", "pérdidas"],
        "pérdidas": ["perdida", "pérdida", "pérdidas"],
        "impuesto_renta": ["impuesto", "renta", "primera_categoria", "segunda_categoria"],
        "primera_categoria": ["primera_categoria"],
        "segunda_categoria": ["segunda_categoria"],
        "tercera_categoria": ["tercera_categoria"],
        "iva": ["iva", "valor_agregado"],
        "credito": ["credito", "crédito", "acreditar"],
        "crédito": ["credito", "crédito", "acreditar"],
        "acreditar": ["credito", "crédito", "acreditar"],
        "deuda_tributaria": ["deuda_tributaria", "tributaria"],
        "tributaria": ["tributaria", "deuda_tributaria"],
        "contribuyente": ["contribuyente"],
        "contribuyentes": ["contribuyente"],
        "residencia": ["residencia", "domicilio", "residir"],
        "domicilio": ["domicilio", "residencia"],
        "fuente": ["fuente"],
        "fuentes": ["fuente"],
        "dividendos": ["dividendo", "dividendos"],
        "dividendo": ["dividendo", "dividendos"],
        "sii": ["sii", "servicio_impuestos_internos"],
        "resolucion": ["resolucion", "resolución"],
        "resolución": ["resolucion", "resolución"],
        "circular": ["circular", "oficio"],
        "oficio": ["oficio", "circular"],
        "jurisprudencia": ["jurisprudencia", "fallo", "sentencia"],
        "fallo": ["jurisprudencia", "fallo", "sentencia"],
        "sentencia": ["jurisprudencia", "fallo", "sentencia"],
        "dl824": ["dl_824", "dl-824"],
        "dl825": ["dl_825", "dl-825"],
        "dl830": ["dl_830", "dl-830"],
        "ley_income": ["dl_824", "dl-824", "renta"],
        "ley_renta": ["dl_824", "dl-824"],
        "codigo_tributario": ["dl_830", "dl-830"],
        "iva_ley": ["dl_825", "dl-825"],
        "articulo": ["articulo", "artículo"],
        "artículo": ["articulo", "artículo"],
        "reinversión": ["reinversion", "reinvertir", "retiro para reinvertir", "reinvertido"],
        "reinversion": ["reinversion", "reinvertir", "retiro para reinvertir", "reinvertido"],
        "reinvertir": ["reinversion", "reinvertir", "retiro para reinvertir", "reinvertido"],
        "inmueble": ["inmueble", "bien raiz", "bien raíz", "enajenación", "enajenacion", "vender casa", "vender departamento", "venta de propiedad"],
        "enajenación": ["enajenación", "enajenacion", "inmueble", "bien raiz", "bien raíz", "vender casa"],
        "persona_natural": ["persona natural", "contribuyente natural", "particular"],
    }

    def __init__(self) -> None:
        self._openai = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        self._law_cache: list[DocumentChunk] | None = None
        self._law_cache_loaded = False

    def _load_law_cache(self) -> None:
        """Carga todos los chunks de leyes en memoria para búsqueda keyword instantánea."""
        if self._law_cache_loaded:
            return
        try:
            response = supabase.table("document_chunks").select("*").eq("source_type", "ley").execute()
            self._law_cache = [DocumentChunk.from_db_row(r) for r in response.data]
            console.print(f"[dim][LAW CACHE] Cargada: {len(self._law_cache)} chunks[/dim]")
        except Exception as e:
            console.print(f"[yellow][WARN] No se pudo cargar cache de leyes: {e}[/yellow]")
            self._law_cache = []
        self._law_cache_loaded = True

    @staticmethod
    def _extract_article_numbers(query: str) -> list[str]:
        """Extrae números de artículo de la query (ej: 'artículo 21' → ['21'])."""
        pattern = re.compile(r"art[íi]culo\s+(\d+[°\w]*)", re.IGNORECASE)
        return [m.group(1).lower().replace("°", "").replace("º", "") for m in pattern.finditer(query)]

    def _keyword_search_local(self, query: str, top_k: int = 10) -> list[SearchResult]:
        """Búsqueda por palabras clave en caché local de leyes (fallback semántico).

        Ranking:
        - Prioriza chunks donde la keyword aparece en el header del artículo.
        - Prioriza chunks con mayor densidad de keywords (más menciones / menos texto).
        - Detecta números de artículo explícitos en la query y da bonus masivo.
        - Asigna una pseudo-similitud competitiva para que compita con resultados
          vectoriales de jurisprudencia que a menudo no son relevantes.
        """
        self._load_law_cache()
        if not self._law_cache:
            return []

        raw_words = re.findall(r"\b\w+\b", query)
        keywords = []
        for w in raw_words:
            w_low = w.lower()
            if w_low in self._STOPWORDS:
                continue
            # Palabras cortas solo si son términos legales conocidos
            if len(w) > 3 or w_low in self._SYNONYMS:
                keywords.append(w_low)

        # Expandir con sinónimos legales
        expanded = set(keywords)
        for kw in keywords:
            if kw in self._SYNONYMS:
                expanded.update(self._SYNONYMS[kw])
        keywords = list(expanded)

        if not keywords:
            return []

        # Extraer números de artículo de la query para bonus específico
        article_nums = self._extract_article_numbers(query)

        scored: list[tuple[float, SearchResult]] = []
        for chunk in self._law_cache:
            content_lower = chunk.content.lower()
            header_lower = (chunk.section_level_name or "").lower()
            uid_lower = chunk.chunk_uid.lower()

            keyword_count = 0
            header_hits = 0
            first_positions: list[int] = []
            for kw in keywords:
                occurrences = content_lower.count(kw)
                keyword_count += occurrences
                if kw in header_lower:
                    header_hits += 1
                pos = content_lower.find(kw)
                if pos >= 0:
                    first_positions.append(pos)

            if keyword_count == 0:
                continue

            # Densidad: menciones por cada 1000 caracteres
            density = keyword_count / max(1, len(chunk.content)) * 1000
            # Bonus por keyword en header (muy relevante)
            header_bonus = header_hits * 0.08
            # Bonus por posición temprana en el texto (definiciones suelen estar al inicio)
            position_bonus = 0.0
            if first_positions:
                min_pos = min(first_positions)
                if min_pos < 200:
                    position_bonus = 0.06
                elif min_pos < 500:
                    position_bonus = 0.03
            # Penalización por chunks muy cortos (notas/modificaciones aisladas)
            content_len = len(chunk.content)
            length_penalty = 0.0
            if content_len < 300:
                length_penalty = -0.14
            elif content_len < 500:
                length_penalty = -0.07

            # Bonus masivo si el número de artículo de la query coincide con el chunk
            article_bonus = 0.0
            for num in article_nums:
                if num in uid_lower or num in header_lower:
                    article_bonus = 0.20
                    break

            # Bonus por law_tag detectado en la query (prioriza la ley correcta)
            law_tag_bonus = 0.0
            query_lower = query.lower()
            if chunk.law_tag == "lir" and any(k in query_lower for k in ("lir", "renta", "dl-824", "dl 824")):
                law_tag_bonus = 0.04
            elif chunk.law_tag == "iva" and any(k in query_lower for k in ("iva", "dl-825", "dl 825")):
                law_tag_bonus = 0.04
            elif chunk.law_tag == "codigo_tributario" and any(k in query_lower for k in ("código tributario", "codigo tributario", "dl-830", "dl 830")):
                law_tag_bonus = 0.04

            # Base + densidad escalada + bonuses + penalizaciones, con tope
            sim = min(0.58, 0.30 + density * 0.10 + header_bonus + position_bonus + length_penalty + article_bonus + law_tag_bonus)
            has_article_match = article_bonus > 0

            scored.append((sim, has_article_match, SearchResult(chunk=chunk, similarity=sim)))

        # Detectar law_tag preferido de la query para desempate
        query_lower = query.lower()
        preferred_law_tag: str | None = None
        if any(k in query_lower for k in ("lir", "renta", "dl-824", "dl 824")):
            preferred_law_tag = "lir"
        elif any(k in query_lower for k in ("iva", "dl-825", "dl 825")):
            preferred_law_tag = "iva"
        elif any(k in query_lower for k in ("código tributario", "codigo tributario", "dl-830", "dl 830")):
            preferred_law_tag = "codigo_tributario"

        # Ordenar por: similitud > artículo coincide > law_tag preferido
        scored.sort(
            key=lambda x: (x[0], x[1], x[2].chunk.law_tag == preferred_law_tag),
            reverse=True,
        )
        return [r for _, _, r in scored[:top_k]]

    def _apply_domain_rules(self, query: str, seen: set[str]) -> list[SearchResult]:
        """Inserta chunks clave por dominio cuando la query semántica/keyword no los alcanza.

        El texto legal usa términos formales (ej: 'artículo 14 letra D') que el usuario
        nunca menciona coloquialmente (ej: 'PRO-PYME', 'pyme').  Estas reglas hardcodean
        los puentes conceptuales más frecuentes para que el RAG no devuelva vacío.
        """
        self._load_law_cache()
        if not self._law_cache:
            return []

        q = query.lower()
        extra: list[SearchResult] = []

        def _force_chunk(uid: str, sim: float = 0.95) -> None:
            if uid in seen:
                return
            for chunk in self._law_cache:
                if chunk.chunk_uid == uid or chunk.chunk_uid.startswith(f"{uid}_"):
                    extra.append(SearchResult(chunk=chunk, similarity=sim))
                    break

        # ── Regla 1: PYME / PRO-PYME + intereses/facilidades/convenios → Art. 192 CT + Art. 14 D LIR ──
        pyme_terms = ["propyme", "pro-pyme", "pyme", "14 letra d", "art. 14 d", "articulo 14 d", "regimen 14 d"]
        interest_terms = ["interes", "convenio", "facilidad", "facilit", "pago", "deuda", "beneficio", "condonacion", "condonación", "cuota", "tesoreria", "plazo"]
        if any(t in q for t in pyme_terms) and any(t in q for t in interest_terms):
            _force_chunk("ley_codigo_tributario_art_192")
            _force_chunk("ley_lir_art_14_d_0")
        elif any(t in q for t in pyme_terms):
            # Solo PYME sin intereses: traer Art. 14 D igual
            _force_chunk("ley_lir_art_14_d_0")

        # ── Regla 2: Gastos rechazados / representación / boletas → Art. 21 LIR ──
        if any(w in q for w in ["rechazado", "rechazados", "rechazar", "gasto", "gastos"]) or any(t in q for t in ["representación", "boleta", "honorario", "arriendo", "contribuciones", "seguro"]):
            _force_chunk("ley_lir_art_21")

        # ── Regla 3: Depreciación / activo fijo / useful life → Art. 31 LIR ──
        if any(w in q for w in ["depreciaci", "depreciar", "depreciación"]) or any(t in q for t in ["activo fijo", "vida útil", "vida util", "amortizaci", "bien de uso", "maquinaria"]):
            _force_chunk("ley_lir_art_31")

        # ── Regla 4: Créditos / crédito fiscal / devolución → Art. 33 bis LIR ──
        if any(t in q for t in ["crédito fiscal", "credito fiscal", "devolución de impuesto", "devolucion de impuesto", "exceso de pago", "retención en exceso"]):
            _force_chunk("ley_lir_art_33_bis")

        # ── Regla 5: Renta presunta / renta deudora / debito → Art. 20 LIR ──
        if any(t in q for t in ["renta presunta", "renta deudora", "renta débito", "renta debito", "presunta", "determinación de oficio"]):
            _force_chunk("ley_lir_art_20")

        # ── Regla 6: Citación SII / fiscalización / requerimiento → Art. 63 CT ──
        if any(t in q for t in ["citación", "citacion", "fiscalizaci", "requerimiento", "ordena", "investigación", "sii"]):
            _force_chunk("ley_codigo_tributario_art_63")

        # ── Regla 7: Liquidación / giro / determinación → Art. 64 CT ──
        if any(t in q for t in ["liquidación", "liquidacion", "giro", "determinación", "determinacion", "oficio de liquidación"]):
            _force_chunk("ley_codigo_tributario_art_64")

        # ── Regla 8: Prescripción / caducidad / plazo → Art. 200-201 CT ──
        if any(t in q for t in ["prescripción", "prescripcion", "caducidad", "plazo para reclamar", "prescribe", "tres años", "6 años"]):
            _force_chunk("ley_codigo_tributario_art_200")
            _force_chunk("ley_codigo_tributario_art_201")

        # ── Regla 9: Infracciones / multa / sanción → Art. 97 CT ──
        if any(t in q for t in ["infracci", "multa", "sanción", "sancion", "pena", "delito tributario"]):
            _force_chunk("ley_codigo_tributario_art_97")

        # ── Regla 10: IVA débito / crédito fiscal → Art. 11 y 12 DL-825 ──
        if any(t in q for t in ["débito fiscal", "debito fiscal", "crédito fiscal", "credito fiscal", "iva", "valor agregado"]):
            _force_chunk("ley_iva_art_11")
            _force_chunk("ley_iva_art_12")

        # ── Regla 11: Retención de IVA → Art. 74 DL-825 ──
        if any(t in q for t in ["retención de iva", "retencion de iva", "retenido", "retener iva"]):
            _force_chunk("ley_iva_art_74")

        # ── Regla 12: Condonación de intereses / rebaja → Art. 56 CT ──
        if any(t in q for t in ["condonación", "condonacion", "rebaja de interes", "rebaja de intereses", "condonar"]):
            _force_chunk("ley_codigo_tributario_art_56")

        # ── Regla 13: Régimen simplificado / 14 letra E / microempresa → Art. 14 E LIR ──
        if any(t in q for t in ["14 letra e", "art. 14 e", "regimen simplificado", "microempresa", "contabilidad simplificada"]):
            _force_chunk("ley_lir_art_14_e")

        # ── Regla 13b: Reinversión de utilidades → NOTA: derogado en reforma 2014 ──
        # El Art. 14 A N° 1 letra c) sobre reinversión fue derogado por Ley 20.780/2014.
        # Ahora los retiros se gravan con impuestos finales sin exención por reinversión.
        # Solo se mantiene histórico en jurisprudencias previas a 2014.
        if any(t in q for t in ["reinversión", "reinversion", "reinvertir", "reinvertido", "retiro para reinvertir", "franquicia tributaria", "aumento efectivo de capital"]):
            # No forzar chunk de ley vigente porque la norma fue derogada
            pass

        # ── Regla 13b-alt: Beneficio del 50% reinvertido → Art. 14 E LIR (microempresas) ──
        if any(t in q for t in ["50% reinvertido", "rebaja base imponible", "rebajar base imponible", "deducción renta líquida", "deduccion renta liquida", "incentivo al ahorro"]):
            _force_chunk("ley_lir_art_14_e_0")

        # ── Regla 13c: Venta de inmueble / persona natural / bien raíz → Art. 17 N° 8 LIR ──
        if any(t in q for t in ["inmueble", "bien raiz", "bien raíz", "vender casa", "vender departamento", "venta de propiedad", "enajenación de bienes raíces", "enajenacion de bienes raices", "ganancia de capital", "plusvalia", "plusvalía"]):
            _force_chunk("ley_lir_art_17_n8")
        if any(t in q for t in ["persona natural", "contribuyente natural"]) and any(t in q for t in ["inmueble", "bien raiz", "bien raíz", "vender", "venta", "casa", "departamento"]):
            _force_chunk("ley_lir_art_17_n8")

        # ── Regla 14: Dividendos / retiro / distribución utilidades → Art. 17 LIR ──
        if any(t in q for t in ["dividendo", "retiro", "distribución de utilidades", "distribucion de utilidades", "remesa de utilidades"]):
            _force_chunk("ley_lir_art_17")

        return extra

    async def search(
        self,
        query: str,
        organization_id: str | None = None,
        source_types: list[str] | None = None,
        law_tags: list[str] | None = None,
        top_k: int | None = None,
        include_derogadas: bool = False,
    ) -> list[SearchResult]:
        """
        Busca chunks relevantes para una consulta.

        Args:
            query: Pregunta o tema de búsqueda
            organization_id: UUID de la organización (None = solo docs públicos)
            source_types: Filtrar por tipo de fuente (ley, circular, jurisprudencia_judicial, etc.)
            law_tags: Filtrar por ley (lir, iva, codigo_tributario)
            top_k: Cantidad de resultados (default 10, max 50)
            include_derogadas: Incluir normas derogadas
        """
        top_k = top_k or self.DEFAULT_TOP_K
        top_k = max(1, min(top_k, 50))

        # 1. Generar embedding de la query
        embedding = await self._embed_query(query)

        # 2. Construir filtros
        # Si hay múltiples source_types o law_tags, hacemos múltiples búsquedas
        # y mergeamos resultados (PostgREST RPC no soporta IN directamente en una sola llamada)
        all_results: list[SearchResult] = []

        source_types_list = source_types or [None]
        law_tags_list = law_tags or [None]

        for st in source_types_list:
            for lt in law_tags_list:
                params = {
                    "query_embedding": embedding,
                    "match_count": top_k,
                    "filter_source_type": st,
                    "filter_law_tag": lt,
                    "include_derogadas": include_derogadas,
                    "filter_organization_id": organization_id,
                }

                try:
                    response = supabase.rpc("match_document_chunks", params).execute()

                    if response.data:
                        for row in response.data:
                            chunk = DocumentChunk.from_db_row(row)
                            similarity = row.get("similarity", 0.0)
                            all_results.append(SearchResult(chunk=chunk, similarity=similarity))
                except Exception as e:
                    console.print(f"[red]❌ Error en búsqueda RAG: {e}[/red]")

        # 3. Deduplicar por chunk_uid y ordenar por similarity
        seen: set[str] = set()
        vector_results: list[SearchResult] = []
        for r in sorted(all_results, key=lambda x: x.similarity, reverse=True):
            if r.chunk.chunk_uid not in seen:
                seen.add(r.chunk.chunk_uid)
                vector_results.append(r)

        vector_results = vector_results[:top_k]

        # 4. Búsqueda híbrida: siempre fusionar keywords de leyes con vectorial.
        # Esto corrige el "style mismatch" donde embeddings conversacionales no
        # alinean con texto normativo denso de leyes chilenas.
        wants_laws = source_types is None or "ley" in source_types
        if wants_laws:
            # Traemos más resultados keyword de lo que necesitamos, para que la fusión
            # tenga más candidatos antes de recortar al top_k final.
            keyword_results = self._keyword_search_local(query, top_k=min(top_k + 8, 25))
            for kr in keyword_results:
                uid = kr.chunk.chunk_uid
                if uid not in seen:
                    seen.add(uid)
                    vector_results.append(kr)
                # Si el chunk ya estaba en vectorial, no bajamos su similitud

            # 5. Reglas de dominio: forzar chunks clave cuando la semántica falla
            # porque el usuario usa términos coloquiales que no aparecen en el texto legal.
            domain_results = self._apply_domain_rules(query, seen)
            for dr in domain_results:
                uid = dr.chunk.chunk_uid
                if uid not in seen:
                    seen.add(uid)
                    vector_results.append(dr)

            # 6. Parent resolution: si un resultado es sub-chunk, traer el padre para contexto completo
            parent_uids: set[str] = set()
            for r in vector_results:
                if r.chunk.parent_chunk_uid:
                    parent_uids.add(r.chunk.parent_chunk_uid)
            for p_uid in parent_uids:
                if p_uid not in seen:
                    chunk = await graph_engine.get_chunk_by_uid(p_uid)
                    if chunk:
                        seen.add(p_uid)
                        vector_results.append(SearchResult(chunk=chunk, similarity=0.75))

            # 7. GraphRAG: traer chunks relacionados por el grafo de conocimiento
            # Si un chunk relevante está conectado a otro (ej: Art. 192 CT menciona Art. 14 D),
            # traemos ese chunk también para que el LLM pueda cruzar leyes.
            graph_uids = graph_engine.expand_results(
                [r.chunk.chunk_uid for r in vector_results],
                top_n=5,
            )
            for uid in graph_uids:
                if uid not in seen:
                    chunk = await graph_engine.get_chunk_by_uid(uid)
                    if chunk:
                        seen.add(uid)
                        vector_results.append(SearchResult(chunk=chunk, similarity=0.82))

            # Reordenar por similitud descendente
            vector_results.sort(key=lambda x: x.similarity, reverse=True)

        return vector_results[:top_k]

    async def search_for_conversation(
        self,
        query: str,
        organization_id: str | None = None,
    ) -> list[SearchResult]:
        """Búsqueda optimizada para modo conversación (top 5, todas las fuentes)."""
        return await self.search(
            query=query,
            organization_id=organization_id,
            top_k=self.CONVERSATION_TOP_K,
            include_derogadas=False,
        )

    async def search_for_document(
        self,
        query: str,
        content_type: str,
        organization_id: str | None = None,
    ) -> list[SearchResult]:
        """
        Búsqueda optimizada para generación de documentos largos.

        Según el tipo de contenido, prioriza ciertas fuentes.
        """
        # Mapeo de content_type a source_types prioritarios
        source_type_map: dict[str, list[str]] = {
            "manual": ["ley", "circular", "jurisprudencia_judicial"],
            "articulo": ["ley", "circular", "jurisprudencia_judicial"],
            "guion": ["ley", "circular", "jurisprudencia_judicial"],
            "historia": ["ley", "jurisprudencia_judicial"],
            "conversacion": ["ley", "circular", "jurisprudencia_judicial", "oficio", "resolucion"],
        }

        source_types = source_type_map.get(content_type, ["ley", "circular", "jurisprudencia_judicial"])

        return await self.search(
            query=query,
            organization_id=organization_id,
            source_types=source_types,
            top_k=self.DEFAULT_TOP_K,
            include_derogadas=False,
        )

    # Mapeo law_tag → nombre legible de la ley
    _LAW_TAG_NAMES: dict[str, str] = {
        "lir": "Ley sobre Impuesto a la Renta (DL-824)",
        "iva": "Ley sobre Impuesto a las Ventas y Servicios (DL-825)",
        "codigo_tributario": "Código Tributario (DL-830)",
    }

    @staticmethod
    def _extract_article_from_uid(chunk_uid: str) -> str | None:
        """Extrae 'Art. 192' de 'ley_codigo_tributario_art_192' o similar."""
        match = re.search(r"_art[_\.]?(\d+(?:[_\-]\d+)?)", chunk_uid, re.IGNORECASE)
        if match:
            num = match.group(1).replace("_", " ").replace("-", " ")
            return f"Art. {num}"
        return None

    async def build_context(self, results: list[SearchResult], query: str = "") -> str:
        """
        Construye un string de contexto a partir de los resultados de búsqueda,
        formateado para ser usado como contexto en prompts de GPT-4o.

        Cada chunk se presenta con metadatos ENRIQUECIDOS ("marcas") para que el LLM
        sepa EXACTAMENTE de qué ley y artículo proviene cada fragmento.
        """
        if not results:
            return "No se encontraron fuentes relevantes en la base de conocimiento."

        lines: list[str] = []
        lines.append("=== FUENTES RELEVANTES ===")
        lines.append("Instrucción: cada fragmento lleva una MARCA que indica su ley, artículo y archivo de origen. "
                     "Usa estas marcas para citar con precisión.\n")

        # ── Notas de dominio: aclaraciones explícitas para conectar conceptos ──
        query_lower = query.lower()
        domain_notes: list[str] = []
        if any(t in query_lower for t in ["propyme", "pro-pyme", "pyme", "14 letra d", "art. 14 d"]):
            domain_notes.append(
                "El régimen PRO-PYME está regulado por el Art. 14 letra D de la LIR (DL-824). "
                "Cuando una fuente mencione 'artículo 14 letra D' o 'contribuyentes sujetos al régimen contenido en el artículo 14 letra D', "
                "se refiere EXPLICITAMENTE al régimen PRO-PYME."
            )
        if any(t in query_lower for t in ["interes", "convenio", "facilidad", "facilit", "pago", "deuda", "condonacion"]):
            domain_notes.append(
                "El Art. 192 del Código Tributario (DL-830) establece beneficios específicos para PRO-PYME: "
                "no se aplican intereses sobre cuotas de convenios de hasta 18 meses, y el pago inicial no puede superar el 5% de la deuda."
            )
        if any(t in query_lower for t in ["gasto rechazado", "gastos rechazados", "representación", "boleta", "honorario"]):
            domain_notes.append(
                "El Art. 21 de la LIR (DL-824) enumera los gastos que son rechazados tributariamente. "
                "Incluye gastos de representación, boletas de honorarios de ciertos profesionales, arriendos, etc."
            )
        if any(t in query_lower for t in ["depreciaci", "activo fijo", "vida útil"]):
            domain_notes.append(
                "El Art. 31 N°5 de la LIR (DL-824) regula la depreciación de bienes de activo fijo. "
                "Establece los porcentajes máximos de depreciación anual según el tipo de bien."
            )
        if any(t in query_lower for t in ["citación", "citacion", "fiscalizaci", "requerimiento"]):
            domain_notes.append(
                "El Art. 63 del Código Tributario (DL-830) regula la citación del SII para fiscalizar. "
                "El Art. 64 regula la liquidación y giro de oficio."
            )
        if any(t in query_lower for t in ["prescripción", "prescripcion", "caducidad", "plazo para reclamar"]):
            domain_notes.append(
                "Los Art. 200 y 201 del Código Tributario (DL-830) regulan la prescripción de la acción tributaria. "
                "Generalmente el plazo es de 3 años desde la fecha de vencimiento de la obligación, o 6 años en ciertos casos."
            )
        if any(t in query_lower for t in ["iva", "débito fiscal", "crédito fiscal", "debito fiscal", "credito fiscal"]):
            domain_notes.append(
                "El IVA está regulado por el DL-825. El Art. 11 regula el débito fiscal y el Art. 12 el crédito fiscal. "
                "La retención de IVA está en el Art. 74."
            )
        if any(t in query_lower for t in ["dividendo", "retiro", "distribución de utilidades"]):
            domain_notes.append(
                "El Art. 17 de la LIR (DL-824) regula la tributación de dividendos y retiros de utilidades. "
                "Desde la reforma tributaria 2014, los dividendos se gravan con el Impuesto Global Complementario o Adicional."
            )
        if any(t in query_lower for t in ["reinversión", "reinversion", "reinvertir", "retiro para reinvertir"]):
            domain_notes.append(
                "IMPORTANTE: El Art. 14 letra A) N° 1 letra c) sobre reinversión de utilidades fue DEROGADO por la Ley 20.780 (reforma tributaria 2014). "
                "Desde 2014, los retiros de utilidades se gravan con impuestos finales sin exención por reinversión. "
                "Las jurisprudencias indexadas que mencionan esta norma son históricas (casos anteriores a 2014)."
            )
        if any(t in query_lower for t in ["50% reinvertido", "rebaja base imponible", "rebajar base imponible", "deducción renta líquida", "incentivo al ahorro"]):
            domain_notes.append(
                "El Art. 14 letra E de la LIR (DL-824) establece un incentivo al ahorro para MICROEMPRESAS (ingresos brutos anuales inferiores a 100.000 UF): "
                "permite deducir de la renta líquida imponible hasta el 50% de la renta líquida imponible que se mantenga invertida en la empresa, "
                "con un tope máximo de 5.000 UF anuales. Solo aplica a contribuyentes de las letras A) y D) del Art. 14."
            )
        if any(t in query_lower for t in ["inmueble", "bien raiz", "bien raíz", "vender casa", "vender departamento", "venta de propiedad", "ganancia de capital", "plusvalia", "plusvalía"]):
            domain_notes.append(
                "El Art. 17 N° 8 de la LIR (DL-824) regula la enajenación de bienes raíces por personas naturales. "
                "La letra b) establece que la parte del mayor valor que no exceda de 8.000 UF es INGRESO NO CONSTITUTIVO DE RENTA (exento). "
                "Si excede 8.000 UF, el excedente se grava con Impuesto Global Complementario o Adicional, o con un impuesto único sustitutivo del 10% a elección del enajenante. "
                "El costo tributario incluye el valor de adquisición reajustado más mejoras declaradas ante el SII."
            )

        if domain_notes:
            lines.append("=== NOTAS DE DOMINIO (interpretación obligatoria) ===")
            for note in domain_notes:
                lines.append(f"• {note}")
            lines.append("")

        for i, r in enumerate(results, 1):
            chunk = r.chunk
            meta = chunk.metadata or {}

            # ── Construir header enriquecido (MARCA) ──
            header_parts: list[str] = [f"[{i}]"]

            # Ley / cuerpo legal
            law_name = self._LAW_TAG_NAMES.get(chunk.law_tag or "", "")
            if law_name:
                header_parts.append(f"LEY: {law_name}")
            elif chunk.source_type:
                header_parts.append(f"TIPO: {chunk.source_type.replace('_', ' ').title()}")

            # Artículo (del UID si es ley)
            article = self._extract_article_from_uid(chunk.chunk_uid)
            if article:
                header_parts.append(f"ARTÍCULO: {article}")
            elif chunk.section_level_name:
                # Limpiar caracteres corruptos comunes en los headers
                clean_header = chunk.section_level_name.replace("�", "")
                header_parts.append(f"SECCIÓN: {clean_header}")

            # Archivo origen
            if chunk.filename:
                header_parts.append(f"ARCHIVO: {chunk.filename}")

            # Score de relevancia (ayuda al LLM a priorizar)
            header_parts.append(f"RELEVANCIA: {r.similarity:.2f}")

            lines.append(" | ".join(header_parts))
            lines.append("-" * 60)

            # Contenido
            content = chunk.content.strip()
            content = re.sub(r"\n{3,}", "\n\n", content)
            lines.append(content)

            # Metadatos adicionales
            meta_parts = []
            if meta.get("fecha"):
                meta_parts.append(f"Fecha: {meta['fecha']}")
            if meta.get("instancia"):
                meta_parts.append(f"Instancia: {meta['instancia']}")
            if meta.get("pdf_url") and meta["pdf_url"] != "N/A":
                meta_parts.append(f"PDF: {meta['pdf_url']}")

            if meta_parts:
                lines.append(f"  → {' | '.join(meta_parts)}")

            lines.append("")  # separador

        return "\n".join(lines)

    async def _embed_query(self, query: str) -> list[float]:
        """Genera embedding para una query de búsqueda."""
        response = await self._openai.embeddings.create(
            model=config.OPENAI_EMBEDDING_MODEL,
            input=[query],
        )
        return response.data[0].embedding


# Instancia global para uso en toda la aplicación
rag = RAGEngine()
