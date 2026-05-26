"""
Motor de escritura inteligente.

Flujo:
1. Detecta tipo de contenido (manual / artículo / guion).
2. Investiga en NotebookLM (preguntas estratégicas con enfoque legal).
3. Redacta contenido largo con GPT-4o usando investigación + agent.md como contexto.
4. Modo outline: genera índice detallado primero para aprobación del usuario.
"""

from __future__ import annotations

import re
from typing import Literal

from openai import AsyncOpenAI
from rich.console import Console

import config
from notebooklm_manager import NotebookLMManager
from settings_store import store as settings_store

console = Console()

ContentType = Literal["manual", "articulo", "guion"]

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
        "Usa formato Markdown (# para título, ## para secciones)."
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
}


class WriterEngine:
    def __init__(self, notebook_name: str | None = None) -> None:
        # Usar notebook_name proporcionado, o leer desde settings, o fallback a config
        name = notebook_name or settings_store.get("primary_notebook_name") or config.NOTEBOOKLM_NOTEBOOK_NAME
        self.nb_manager = NotebookLMManager(notebook_name=name)
        self._nb_id: str | None = None
        self._openai = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        self._agent_md = _load_agent_md()

    async def _ensure_notebook(self) -> str:
        if self._nb_id is None:
            self._nb_id = await self.nb_manager.create_or_get_notebook()
        return self._nb_id

    def set_notebook(self, name: str) -> None:
        """Cambia el notebook activo (útil para alternar entre cuadernos)."""
        self.nb_manager = NotebookLMManager(notebook_name=name)
        self._nb_id = None

    @staticmethod
    def detect_content_type(prompt: str) -> ContentType:
        p = prompt.lower()
        guion_keywords = [
            "guion", "guión", "video", "youtube", "tiktok", "reel",
            "escena", "plano", "dialogo", "diálogo", "voz en off",
        ]
        manual_keywords = [
            "manual", "guia", "guía", "libro", "capitulo", "capítulo",
            "ebook", "e-book", "compendio", "tratado",
        ]
        if any(k in p for k in guion_keywords):
            return "guion"
        if any(k in p for k in manual_keywords):
            return "manual"
        return "articulo"

    async def research(self, topic: str) -> str:
        """Investiga en NotebookLM con preguntas legales específicas."""
        nb_id = await self._ensure_notebook()
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
                result = await self.nb_manager.ask_question(nb_id, q)
                answer = result.get("answer", "")
                return answer if answer and len(answer) > 50 else ""
            except Exception as e:
                console.print(f"  [yellow]⚠️ Research {i} falló: {e}[/yellow]")
                return ""

        # Ejecutar todas las preguntas en paralelo con timeout global
        tasks = [asyncio.create_task(_ask_one(i, q)) for i, q in enumerate(questions, 1)]
        try:
            answers = await asyncio.wait_for(asyncio.gather(*tasks), timeout=60.0)
        except asyncio.TimeoutError:
            console.print("[yellow]⚠️ Research global timeout (60s). Usando respuestas parciales.[/yellow]")
            answers = [t.result() if t.done() else "" for t in tasks]

        findings = [a for a in answers if a]
        return "\n\n---\n\n".join(findings) if findings else ""

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

        response = await self._openai.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        return (response.choices[0].message.content or "").strip()

    async def write(
        self,
        topic: str,
        research_ctx: str,
        content_type: ContentType,
        outline: str = "",
    ) -> str:
        """Genera el contenido completo con GPT-4o."""
        system = _SYSTEM_PROMPTS[content_type]
        if self._agent_md:
            system += f"\n\nINSTRUCCIONES ADICIONALES DEL AGENTE:\n{self._agent_md}"

        user_prompt = (
            f"Tema a desarrollar: {topic}\n\n"
            f"Información de fuentes (NotebookLM):\n{research_ctx}\n\n"
        )
        if outline:
            user_prompt += f"Índice aprobado por el usuario:\n{outline}\n\n"
        user_prompt += (
            "Escribe el contenido COMPLETO, EXTENSO y PROFUNDO siguiendo el índice si existe. "
            "NO te quedes corto. NO resumas. Desarrolla cada punto con máximo detalle. "
            "Usa TODOS los tokens disponibles para entregar el manual más completo posible. "
            "No agregues notas al pie ni disclaimers sobre ser IA. "
            "Solo entrega el contenido profesional listo para publicar. "
            "Recuerda: cada subcapítulo debe tener definición, base legal, desarrollo profundo, ejemplo práctico extenso y tip."
        )

        console.print(f"  [dim]✍️ GPT-4o redactando ({content_type})...[/dim]")
        response = await self._openai.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=config.WRITER_TEMPERATURE,
            max_tokens=config.WRITER_MAX_TOKENS,
        )
        return (response.choices[0].message.content or "").strip()

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
