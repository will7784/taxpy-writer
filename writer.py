"""
Motor de escritura inteligente.

Flujo:
1. Detecta tipo de contenido (manual / artículo / guion).
2. Investiga en RAG Supabase pgvector (búsqueda semántica de fuentes legales).
3. Redacta contenido largo con GPT-4o usando investigación + agent.md como contexto.
4. Modo outline: genera índice detallado primero para aprobación del usuario.
"""

from __future__ import annotations

import re
from typing import Literal

from llm_client import LLMClient
from rich.console import Console

import config
from notebooklm_manager import NotebookLMManager
from rag_engine import rag as rag_engine
from citation_guardrail import guardrail_check
from settings_store import store as settings_store

console = Console()

ContentType = Literal["manual", "articulo", "guion", "historia", "conversacion"]

# ── Cargar instrucciones del agente ───────────────────────
_AGENT_MD: str = ""


def _load_agent_md() -> str:
    global _AGENT_MD
    if not _AGENT_MD and config.AGENT_MD_FILE.exists():
        _AGENT_MD = config.AGENT_MD_FILE.read_text(encoding="utf-8")
    return _AGENT_MD


_SYSTEM_PROMPTS: dict[ContentType, str] = {
    "manual": (
        "Eres un experto tributario chileno y redactor técnico senior. "
        "Escribe un manual EXHAUSTIVO, profundo y riguroso. Este manual debe ser "
        "suficientemente extenso y detallado como para publicarse como ebook o libro. "
        "\n\nREQUISITOS DE EXTENSIÓN Y PROFUNDIDAD:\n"
        "- Mínimo 5 capítulos principales, cada uno con 3-5 subcapítulos.\n"
        "- Cada subcapítulo debe tener entre 400 y 800 palabras de desarrollo puro.\n"
        "- El manual completo debe superar las 3000 palabras. Usa TODOS los tokens disponibles.\n"
        "- NO resumas. NO te quedes corto. Si te quedan tokens, sigue desarrollando.\n"
        "\n\nESTRUCTURA OBLIGATORIA POR SUBCAPÍTULO:\n"
        "1. Definición clara y completa del concepto.\n"
        "2. Base legal específica con artículo, ley, oficio o circular (cita textual si aplica).\n"
        "3. Desarrollo explicativo paso a paso, con profundidad (no superficial).\n"
        "4. Errores comunes que cometen los contribuyentes y cómo evitarlos.\n"
        "5. Ejemplo práctico DESARROLLADO de mínimo 2-3 párrafos con sujetos ficticios, montos, fechas y resultado concreto.\n"
        "6. Tip práctico al final del subcapítulo (\"Para evitar problemas, recuerda que...\").\n"
        "\n\nREGLA ABSOLUTA DE GROUNDING:\n"
        "- Solo usa la información de las FUENTES proporcionadas.\n"
        "- ESTÁS PROHIBIDO de inventar artículos, requisitos, plazos, montos o exenciones.\n"
        "- NO mezcles normas de otros países (ej: no existe 'exención por vivienda habitual' ni 'periodo mínimo de posesión' para inmuebles en Chile).\n"
        "- Si la fuente no menciona algo, NO lo menciones tú. Di: 'No tengo esa información en mis fuentes.'\n"
        "\n\nREGLAS DE ESTILO:\n"
        "- Usa formato Markdown (# Capítulo, ## Subcapítulo, ### Apartado).\n"
        "- Cita siempre la norma entre paréntesis después de cada afirmación de derecho.\n"
        "- Tono profesional pero accesible para contadores y abogados.\n"
        "- NUNCA uses frases como 'en resumen', 'para concluir' antes del final.\n"
        "- NUNCA repitas información entre subcapítulos.\n"
        "- Al final del manual incluye una sección de Referencias Normativas listando todas las normas citadas."
    ),
    "articulo": (
        "Eres un experto tributario chileno y columnista especializado. "
        "Escribe un artículo largo, editorial y didáctico con rigor técnico. "
        "Estructura: introducción hook, desarrollo con subtemas, conclusión con take-away. "
        "Cada sección debe incluir citas normativas específicas (artículo, ley, oficio SII), "
        "al menos 2 ejemplos prácticos desarrollados con sujetos, hechos y resultado, "
        "y tips prácticos al final de cada sección. "
        "Tono: claro, profesional, accesible para contadores y abogados. "
        "Usa formato Markdown (# para título, ## para secciones).\n\n"
        "REGLA ABSOLUTA: Solo usa información de las fuentes proporcionadas. "
        "NO inventes artículos, requisitos, plazos ni exenciones. "
        "NO mezcles normas de otros países con Chile."
    ),
    "guion": (
        "Eres un experto tributario chileno y guionista de video educativo. "
        "Escribe un guion completo para video de YouTube/TikTok/Reel sobre el tema solicitado. "
        "Formato obligatorio por escena:\n"
        "ESCENA X\n"
        "PLANO: [plano cinematográfico]\n"
        "DIÁLOGO/VO: [texto a decir]\n"
        "GRÁFICO: [lo que aparece en pantalla]\n\n"
        "Incluye hook inicial, desarrollo didáctico y CTA final. "
        "Cada escena debe ser de 30–60 segundos. "
        "Tono conversacional pero riguroso. Máximo 3–5 minutos de video."
    ),
    "historia": (
        "Eres un experto tributario chileno y narrador de historias. "
        "Tu misión es transformar un tema fiscal denso en una historia amena, clara y memorable, "
        "contada como un monólogo narrativo en primera o tercera persona. "
        "Imagina que estás frente a una audiencia curiosa pero sin formación técnica, "
        "y necesitas que entiendan el tema a fondo sin aburrirlos.\n\n"
        "ESTRUCTURA NARRATIVA OBLIGATORIA:\n"
        "1. HOOK INICIAL: Comienza con una situación cotidiana, una anécdota o una pregunta provocadora "
        "que conecte emocionalmente con el lector y plantee el problema fiscal de forma tangible.\n"
        "2. PERSONAJE/NARRADOR: Usa un personaje ficticio con nombre propio (empresario, contador, contribuyente común) "
        "o un narrador cercano que guíe la historia de principio a fin. El lector debe sentir que 'conoce' a alguien en esta historia.\n"
        "3. DESARROLLO NARRATIVO CON RIGOR LEGAL: Avanza la historia paso a paso. Cada vez que introduces una regla fiscal, "
        "no la sueltes como definición seca; intégrala en la trama del personaje. "
        "Y aquí viene lo crucial: CADA afirmación de derecho debe ir respaldada con su cita legal exacta "
        "(artículo, ley, decreto, oficio o circular) entre paréntesis, justo después del concepto. "
        "Ejemplo: 'A Juan le tocaba pagar el impuesto antes del 20 del mes siguiente (Artículo 59 del Código Tributario, Decreto Ley N° 830)'.\n"
        "4. CONFLICTO O TENSIÓN: Presenta un obstáculo, una duda, un error común o una consecuencia inesperada. "
        "¿Qué pasa si no cumple? ¿Qué opción tiene A o B? Esto crea engagement.\n"
        "5. DESENLACE CON APRENDIZAJE: Resuelve la historia con claridad. El personaje aprende, actúa o toma una decisión informada. "
        "El lector debe quedarse con una comprensión real del tema, no solo entretenido.\n"
        "6. CIERRE REFLEXIVO: Termina con una frase memorable, un tip práctico o una pregunta que invite a reflexionar sobre la norma.\n\n"
        "REGLAS DE ESTILO:\n"
        "- Tono: cercano, conversacional, como un buen conversador que sabe de impuestos. Puedes usar humor sutil, ironía suave o expresiones coloquiales, pero nunca falta de respeto al tema.\n"
        "- NO uses jerga fiscal sin explicarla. Cuando aparezca un término técnico, incluye una analogía o explicación breve en el mismo párrafo.\n"
        "- Las citas legales son OBLIGATORIAS y deben integrarse de forma natural en la narrativa, no como notas al pie.\n"
        "- Incluye al menos UN EJEMPLO PRÁCTICO DESARROLLADO con montos, fechas y nombres ficticios concretos.\n"
        "- Usa formato Markdown (# para título, ## para secciones de la historia).\n"
        "- NO repitas información. NO uses frases genéricas de cierre como 'en conclusión'. El cierre debe ser orgánico a la historia.\n"
        "- Extensión mínima: 1500 palabras. Desarrolla la historia con generosidad. Si te quedan tokens, profundiza en las consecuencias legales o en las alternativas del personaje.\n"
        "- Al final del texto, incluye una breve sección 'Referencias Normativas' listando todas las normas citadas en la historia."
    ),
    "conversacion": (
        "Eres ClaudIA, una experta tributaria chilena con un tono amigable, cercano y conversacional. "
        "Respondes como si estuvieras hablando por teléfono con un amigo que te hace una consulta fiscal. "
        "Tu objetivo es que entienda el tema sin sentirse abrumado, manteniendo siempre el sustento legal.\n\n"
        "REGLA ABSOLUTA: Solo usa información de las fuentes proporcionadas. "
        "Si algo no está en las fuentes, di 'No tengo esa información en mis fuentes indexadas.' "
        "NO inventes artículos, requisitos ni exenciones.\n\n"
        "REGLAS DE ESTILO OBLIGATORIAS:\n"
        "- Usa frases CORTAS y directas. Máximo 15-20 palabras por frase.\n"
        "- NUNCA uses markdown: no #, no ##, no negritas, no bullets, no numeración, no listas.\n"
        "- Usa conectores naturales de conversación: 'mira', 'fíjate que', 'o sea', 'entonces', 'la cosa es', 'básicamente'.\n"
        "- Incluye pausas naturales: comas, puntos suspensivos... como cuando hablas en persona.\n"
        "- Cita las normas de forma ORAL e integrada en la conversación. Ejemplo: 'según el artículo 21 de la Ley de Renta, o sea el decreto ley 824, tú puedes deducir eso sin problema'.\n"
        "- Máximo 250 palabras en total. Sé concisa pero completa. No te quedes corta en la explicación legal.\n"
        "- Si el tema lo permite, termina con una pregunta breve para invitar a seguir conversando.\n"
        "- NO digas 'en conclusión', 'para resumir', ni frases de cierre académico.\n"
        "- NO te presentes como IA. Solo responde como ClaudIA, la experta.\n"
        "- NO agregues disclaimers sobre ser inteligencia artificial."
    ),
}


class WriterEngine:
    def __init__(self, notebook_name: str | None = None) -> None:
        # NotebookLM legacy (lazy init, se eliminará en Fase 5)
        self._notebook_name = notebook_name or settings_store.get("primary_notebook_name") or config.NOTEBOOKLM_NOTEBOOK_NAME
        self._nb_manager: NotebookLMManager | None = None
        self._nb_id: str | None = None

        self._llm = LLMClient()
        self._agent_md = _load_agent_md()

    def _get_nb_manager(self) -> NotebookLMManager | None:
        """Lazy initialization de NotebookLMManager. Retorna None si no está instalado."""
        if self._nb_manager is None:
            try:
                self._nb_manager = NotebookLMManager(notebook_name=self._notebook_name)
            except Exception as e:
                console.print(f"[dim]NotebookLM no disponible: {e}[/dim]")
                return None
        return self._nb_manager

    async def _ensure_notebook(self) -> str | None:
        if self._nb_id is None:
            nb = self._get_nb_manager()
            if nb:
                self._nb_id = await nb.create_or_get_notebook()
        return self._nb_id

    @staticmethod
    def detect_content_type(prompt: str) -> ContentType:
        # Normalizar: quitar signos de apertura, espacios, y convertir a minúsculas
        p = prompt.lower().strip().lstrip("¿¡")

        # 1. Detectar conversación casual (saludos, mensajes cortos, preguntas directas)
        conversation_signals = [
            "hola", "buenos días", "buenas tardes", "buenas noches",
            "cómo estás", "como estas", "qué tal", "que tal",
            "gracias", "de nada", "adiós", "chao",
            "me puedes ayudar", "tengo una duda", "quiero preguntar",
            "qué opinas", "que opinas", "explicame", "explícame",
        ]
        # Mensajes muy cortos (< 40 chars) o que empiezan con saludo → conversación
        if len(p) < 40 or any(p.startswith(s) for s in conversation_signals):
            return "conversacion"

        # 2. Detectar tipos de documento por keywords
        guion_keywords = [
            "guion", "guión", "video", "youtube", "tiktok", "reel",
            "escena", "plano", "dialogo", "diálogo", "voz en off",
        ]
        manual_keywords = [
            "manual", "guia", "guía", "libro", "capitulo", "capítulo",
            "ebook", "e-book", "compendio", "tratado",
        ]
        historia_keywords = [
            "historia", "monologo", "monólogo", "storytelling", "narrativa",
            "cuento", "relato", "anécdota", "anecdota", "cronica", "crónica",
            "novela", "dramatiza", "dramatizar", "como si fuera",
            "imagina que", "había una vez", "cuéntame como historia",
        ]
        if any(k in p for k in guion_keywords):
            return "guion"
        if any(k in p for k in manual_keywords):
            return "manual"
        if any(k in p for k in historia_keywords):
            return "historia"

        # 3. Si parece una pregunta directa (qué, cómo, cuándo, por qué) y no es largo → conversación
        question_starters = ["qué ", "que ", "cómo ", "como ", "cuándo ", "cuando ", "por qué ", "por que ", "cuál ", "cual ", "dónde ", "donde ", "quién ", "quien "]
        if any(p.startswith(q) for q in question_starters) and len(p) < 120:
            return "conversacion"

        return "articulo"

    async def research(self, topic: str, content_type: ContentType = "articulo") -> str:
        """Investiga en RAG Supabase con búsqueda semántica."""
        console.print(f"  [dim]🔍 Buscando en RAG: {topic}...[/dim]")

        try:
            # Búsqueda semántica en todas las fuentes relevantes
            results = await rag_engine.search_for_document(topic, content_type)
            if not results:
                console.print("  [yellow]⚠️ No se encontraron fuentes en RAG[/yellow]")
                return ""

            context = await rag_engine.build_context(results, query=topic)
            console.print(f"  [dim]✓ {len(results)} fuentes encontradas[/dim]")
            return context
        except Exception as e:
            console.print(f"  [yellow]⚠️ RAG research falló: {e}[/yellow]")
            # Fallback legacy: intentar NotebookLM
            try:
                return await self._research_legacy(topic)
            except Exception:
                return ""

    async def generate_outline(
        self,
        topic: str,
        research_ctx: str,
        content_type: ContentType,
    ) -> str:
        """Genera un índice detallado para aprobación del usuario."""
        system = (
            "Eres un editor experto en derecho tributario chileno. "
            "Genera un índice detallado (outline) para un documento tributario. "
            "Incluye capítulos, subcapítulos y una línea descriptiva de qué cubrirá cada uno. "
            "Asegúrate de que cada sección tenga soporte legal (artículos, oficios, jurisprudencia)."
        )
        user_prompt = (
            f"Tema: {topic}\n\n"
            f"Información de fuentes:\n{research_ctx}\n\n"
            f"Tipo de documento: {content_type}\n\n"
            "Genera un índice detallado en formato lista numerada. "
            "Cada ítem debe tener: título + 1 línea de descripción."
        )

        content = await self._llm.chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        return content.strip()

    async def _extract_facts(self, research_ctx: str, topic: str) -> str:
        """
        Paso intermedio: extrae hechos verificables de las fuentes.
        Esto fuerza al LLM a leer los chunks ANTES de escribir,
        reduciendo drásticamente alucinaciones.
        """
        system = (
            "Eres un extractor de hechos legales. Tu trabajo es LEER las fuentes proporcionadas "
            "y extraer SOLAMENTE los hechos concretos, con sus citas exactas. "
            "NO inventes nada. Si algo no está en las fuentes, NO lo incluyas.\n\n"
            "FORMATO DE SALIDA (lista numerada):\n"
            "1. [HECHO] + [CITA EXACTA: Artículo X, Ley Y, Decreto Z]\n"
            "2. [HECHO] + [CITA EXACTA]\n"
            "...\n\n"
            "REGLAS:\n"
            "- Incluye montos numéricos exactos (UF, porcentajes, topes).\n"
            "- Incluye requisitos específicos mencionados en las fuentes.\n"
            "- Incluye las opciones o alternativas que la norma presente.\n"
            "- Si una fuente dice 'exento hasta X UF', incluye ese número exacto.\n"
            "- NO agregues interpretaciones ni conclusiones. Solo hechos brutos de las fuentes."
        )
        user_prompt = (
            f"Tema: {topic}\n\n"
            f"Fuentes legales (lee cada una y extrae los hechos):\n{research_ctx}\n\n"
            "Extrae la lista de hechos verificables con sus citas exactas."
        )
        try:
            facts = await self._llm.chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=2000,
            )
            return facts.strip()
        except Exception as e:
            console.print(f"[yellow]⚠️ Extracción de hechos falló: {e}[/yellow]")
            return ""

    async def write(
        self,
        topic: str,
        research_ctx: str,
        content_type: ContentType,
        outline: str = "",
    ) -> str:
        """Genera el contenido completo con GPT-4o usando extracción de hechos previa."""
        # ── PASO 1: Extraer hechos verificables de las fuentes ──
        # Esto es crítico para evitar alucinaciones: forzamos al LLM a leer
        # los chunks antes de escribir.
        facts = ""
        if content_type in ("articulo", "manual", "historia", "conversacion"):
            console.print(f"  [dim]🔍 Extrayendo hechos de fuentes...[/dim]")
            facts = await self._extract_facts(research_ctx, topic)
            if facts:
                console.print(f"  [dim]✓ {facts.count(chr(10))} hechos extraídos[/dim]")

        # ── PASO 2: Construir prompt con hechos + fuentes originales ──
        system = _SYSTEM_PROMPTS[content_type]
        if self._agent_md:
            system += f"\n\nINSTRUCCIONES ADICIONALES DEL AGENTE:\n{self._agent_md}"

        # Agregar few-shot específico para inmuebles si aplica
        if any(k in topic.lower() for k in ("inmueble", "bien raiz", "bien raíz", "venta propiedad", "enajenación")):
            system += (
                "\n\nEJEMPLO DE RESPUESTA CORRECTA (tema: venta de inmuebles):\n"
                "'La ganancia de capital obtenida por una persona natural en la enajenación de bienes raíces "
                "está exenta hasta 8.000 UF, siempre que el bien haya sido adquirido con anterioridad al 1° de enero de 2004 "
                "o se trate de una enajenación posterior a esa fecha sujeta a las normas transitorias. "
                "El excedente sobre las 8.000 UF se grava con el IGC o el Impuesto Adicional, "
                "o bien el contribuyente puede optar por un impuesto único sustitutivo del 10% sobre la ganancia de capital. "
                "(Artículo 17 N° 8, Ley sobre Impuesto a la Renta, DL-824).'\n\n"
                "EJEMPLO DE RESPUESTA INCORRECTA (NUNCA hagas esto):\n"
                "'Existe una exención por vivienda habitual si se ha vivido más de un año en la propiedad.' → "
                "ESTO ES FALSO EN CHILE. NO existe ese concepto en la LIR.\n\n"
                "EJEMPLO DE RESPUESTA INCORRECTA (NUNCA hagas esto):\n"
                "'Debes poseer el inmueble por un periodo mínimo para acceder a la exención.' → "
                "ESTO ES FALSO EN CHILE. NO existe periodo mínimo de posesión para inmuebles en la LIR.\n\n"
            )

        user_prompt = (
            f"Tema a desarrollar: {topic}\n\n"
        )
        if facts:
            user_prompt += (
                f"=== HECHOS EXTRAÍDOS DE LAS FUENTES (USAR SOLO ESTO) ===\n{facts}\n"
                f"=== FIN HECHOS ===\n\n"
            )
        user_prompt += (
            f"Información de fuentes (base de conocimiento):\n{research_ctx}\n\n"
        )
        if outline:
            user_prompt += f"Índice aprobado por el usuario:\n{outline}\n\n"
        user_prompt += (
            "REGLA ABSOLUTA: Solo usa la información de las FUENTES y los HECHOS EXTRAÍDOS arriba. "
            "NO inventes artículos, leyes, decretos, oficios, circulares ni jurisprudencia que no "
            "aparezcan en las fuentes. Cita SIEMPRE la norma exacta con artículo, ley y número de decreto.\n\n"
            "REGLA CRÍTICA: Si un hecho no está en las fuentes proporcionadas, NO lo menciones. "
            "NO uses conocimiento general. NO apliques normas de otros países (España, Argentina, etc.) a Chile. "
            "Si no tienes información en las fuentes para responder algo, di: 'No tengo esa información en mis fuentes.'\n\n"
            "Escribe el contenido COMPLETO, EXTENSO y PROFUNDO siguiendo el índice si existe. "
            "NO te quedes corto. NO resumas. Desarrolla cada punto con máximo detalle. "
            "Usa TODOS los tokens disponibles para entregar el manual más completo posible. "
            "No agregues notas al pie ni disclaimers sobre ser IA. "
            "Solo entrega el contenido profesional listo para publicar. "
            "Recuerda: cada subcapítulo debe tener definición, base legal, desarrollo profundo, ejemplo práctico extenso y tip."
        )

        provider_label = self._llm.provider.upper()
        console.print(f"  [dim]✍️ {provider_label} redactando ({content_type})...[/dim]")
        content = await self._llm.chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=config.WRITER_TEMPERATURE,
            max_tokens=config.WRITER_MAX_TOKENS,
        )
        content = content.strip()

        # Guardrail: verificar citas legales contra el contexto de fuentes
        try:
            content = guardrail_check(research_ctx, content)
        except Exception as e:
            console.print(f"[yellow]⚠️ Guardrail falló (no crítico): {e}[/yellow]")

        return content

    async def _research_legacy(self, topic: str) -> str:
        """Fallback: investiga en NotebookLM (deprecated)."""
        nb_id = await self._ensure_notebook()
        if not nb_id:
            return ""

        nb = self._get_nb_manager()
        if not nb:
            return ""

        questions = [
            (
                f"Responde como experto tributario chileno sobre: {topic}. "
                "Incluye EXACTAMENTE: (a) artículos del Código Tributario y Ley de Renta aplicables con números exactos, "
                "(b) oficios o circulares del SII relacionados con números y fechas, (c) jurisprudencia relevante si existe. "
                "Cita siempre el número de artículo y la norma. Sé exhaustivo, no resumas."
            ),
            (
                f"¿Qué errores cometen los contribuyentes respecto a {topic}? "
                "Incluye sanciones, plazos legales, consecuencias de incumplimiento y casos reales si los conoces. "
                "Cita artículos específicos del Código Tributario."
            ),
            (
                f"Proporciona un ejemplo práctico COMPLETO y DESARROLLADO sobre {topic}: "
                "sujeto (empresa o persona con nombre ficticio), hechos concretos (montos, fechas, montos exactos), "
                "aplicación de la norma paso a paso y resultado final con cifras. "
                "Incluye la base legal aplicable al caso. El ejemplo debe tener mínimo 200 palabras."
            ),
            (
                f"¿Cuáles son los pasos prácticos, trámites, formularios y plazos específicos "
                f"que un contribuyente debe seguir respecto a {topic}? "
                "Incluye formularios SII, plazos en días hábiles, y requisitos documentales."
            ),
            (
                f"¿Existen excepciones, beneficios tributarios o régimenes especiales aplicables a {topic}? "
                "Incluye montos de exención, topes, porcentajes específicos y artículos de ley."
            ),
        ]
        findings: list[str] = []
        import asyncio

        async def _ask_one(i: int, q: str) -> str:
            try:
                console.print(f"  [dim]🔍 NotebookLM research {i}/{len(questions)}...[/dim]")
                result = await nb.ask_question(nb_id, q)
                answer = result.get("answer", "")
                return answer if answer and len(answer) > 50 else ""
            except Exception as e:
                console.print(f"  [yellow]⚠️ Research {i} falló: {e}[/yellow]")
                return ""

        tasks = [asyncio.create_task(_ask_one(i, q)) for i, q in enumerate(questions, 1)]
        try:
            answers = await asyncio.wait_for(asyncio.gather(*tasks), timeout=60.0)
        except asyncio.TimeoutError:
            console.print("[yellow]⚠️ Research global timeout (60s). Usando respuestas parciales.[/yellow]")
            answers = [t.result() if t.done() else "" for t in tasks]

        findings = [a for a in answers if a]
        return "\n\n---\n\n".join(findings) if findings else ""

    @staticmethod
    def split_for_telegram(text: str, max_len: int = 4000) -> list[str]:
        """Divide texto largo en chunks respetando párrafos."""
        if len(text) <= max_len:
            return [text]
        chunks: list[str] = []
        current = ""
        for paragraph in text.split("\n\n"):
            if len(current) + len(paragraph) + 2 > max_len:
                if current:
                    chunks.append(current.strip())
                if len(paragraph) > max_len:
                    lines = paragraph.split("\n")
                    current = ""
                    for line in lines:
                        if len(current) + len(line) + 1 > max_len:
                            chunks.append(current.strip())
                            current = line
                        else:
                            current += "\n" + line if current else line
                else:
                    current = paragraph
            else:
                current += "\n\n" + paragraph if current else paragraph
        if current:
            chunks.append(current.strip())
        return chunks
