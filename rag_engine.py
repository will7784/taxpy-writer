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
    CONVERSATION_TOP_K = 8  # Aumentado: chunks de leyes ahora son más pequeños y precisos

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
            console.print(f"[dim]📚 Caché de leyes cargada: {len(self._law_cache)} chunks[/dim]")
        except Exception as e:
            console.print(f"[yellow]⚠️ No se pudo cargar caché de leyes: {e}[/yellow]")
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

        # ── Regla 1: PYME / PRO-PYME + intereses/facilidades/convenios → Art. 192 CT ──
        # El Art. 192 CT menciona 'artículo 14 letra D' pero NUNCA la palabra 'propyme'.
        pyme_terms = ["propyme", "pro-pyme", "pyme", "14 letra d", "art. 14 d", "articulo 14 d", "regimen 14 d"]
        interest_terms = ["interes", "convenio", "facilidad", "facilit", "pago", "deuda", "beneficio", "condonacion", "condonación", "cuota", "tesoreria", "plazo"]
        if any(t in q for t in pyme_terms) and any(t in q for t in interest_terms):
            target_uid = "ley_codigo_tributario_art_192"
            if target_uid not in seen:
                for chunk in self._law_cache:
                    if chunk.chunk_uid == target_uid:
                        extra.append(SearchResult(chunk=chunk, similarity=0.95))
                        break

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
            keyword_results = self._keyword_search_local(query, top_k=8)
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

    async def build_context(self, results: list[SearchResult]) -> str:
        """
        Construye un string de contexto a partir de los resultados de búsqueda,
        formateado para ser usado como contexto en prompts de GPT-4o.
        """
        if not results:
            return "No se encontraron fuentes relevantes en la base de conocimiento."

        lines: list[str] = []
        lines.append("=== FUENTES RELEVANTES ===\n")

        for i, r in enumerate(results, 1):
            chunk = r.chunk
            meta = chunk.metadata or {}

            # Header con fuente y score
            header_parts = [f"[{i}]"]
            if chunk.source_type:
                header_parts.append(chunk.source_type.replace("_", " ").title())
            if chunk.section_level_name:
                header_parts.append(f"— {chunk.section_level_name}")
            if meta.get("codigo_pronunciamiento"):
                header_parts.append(f"({meta['codigo_pronunciamiento']})")

            lines.append(" | ".join(header_parts))

            # Contenido
            content = chunk.content.strip()
            # Limpiar exceso de saltos de línea
            content = re.sub(r"\n{3,}", "\n\n", content)
            lines.append(content)

            # Metadatos adicionales relevantes
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
