"""
Bot de Telegram — Taxpy RAG.

Usa Supabase pgvector + GPT-4o para responder consultas tributarias
con fuentes legales verificables (leyes, circulares, jurisprudencia SII).
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from rich.console import Console

import config
import exporter
import live_lookup
from rag_engine import rag as rag_engine
from settings_store import store as settings_store
from supabase_client import supabase
from voice_processor import VoiceProcessor
from citation_guardrail import guardrail_check
from decision_engine import engine as decision_engine
from writer import WriterEngine, _load_agent_md


def _log_query(chat_id: int, text: str) -> None:
    """Registra la consulta real en usage_logs (best-effort, nunca bloquea el chat).

    Base para scripts/eval_graph_lift.py — sin esto no hay forma de medir
    con evidencia si el grafo de conocimiento aporta sobre consultas reales.
    Requiere la columna query_text (sql/002_usage_logs_query_text.sql).
    """
    try:
        supabase.table("usage_logs").insert({
            "telegram_chat_id": chat_id,
            "query_type": "chat",
            "query_text": text[:2000],
        }).execute()
    except Exception:
        pass

console = Console()


def _sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-zA-Z0-9\-_]+", "_", name).strip("_")
    return name or "documento"


class SessionStore:
    """Persiste sesiones en SQLite para sobrevivir reinicios del bot."""

    def __init__(self, db_path) -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_chat ON sessions(chat_id)"
            )

    def save(self, chat_id: int, title: str, content: str, content_type: str) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO sessions (chat_id, title, content, type, created_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, title, content, content_type, datetime.utcnow().isoformat()),
            )

    def get_latest(self, chat_id: int) -> dict | None:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT title, content, type FROM sessions WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
        if not row:
            return None
        return {"title": row[0], "content": row[1], "type": row[2]}


class WriterTelegramBot:
    def __init__(self, token: str) -> None:
        self.token = token
        self.writer = WriterEngine()
        self.voice = VoiceProcessor() if config.OPENAI_API_KEY else None
        self._store = SessionStore(config.TELEGRAM_DB_PATH)
        # Sesiones en memoria: chat_id -> dict
        self._sessions: dict[int, dict] = {}

    # ── Comandos ──────────────────────────────────────────────

    async def _start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        text = (
            "🤖 *ClaudIA — Asistente Tributario Chileno*\n\n"
            "Respondo consultas de derecho tributario chileno con *precisión legal* "
            "usando árboles de decisión validados + fuentes oficiales.\n\n"
            "*Árboles disponibles (Código Tributario):*\n"
            "• Citación SII para fiscalizar (Art. 63)\n"
            "• Liquidación y giro de oficio (Art. 64-65)\n"
            "• Determinación de oficio / Renta presunta (Art. 59-61)\n"
            "• Prescripción de la acción tributaria (Art. 200-201)\n"
            "• Infracciones y sanciones (Art. 97-98)\n"
            "• Recurso de reposición y reclamación (Art. 120-122)\n"
            "• Intereses y reajustes por mora (Art. 53-54)\n"
            "• Cobranza ejecutiva y embargo (Art. 172-177)\n"
            "• Secreto tributario y acceso a info (Art. 35-37)\n"
            "• Convenio de pago y facilidades (Art. 56, 192)\n\n"
            "*Comandos:*\n"
            "• /fuentes — info de la base de conocimiento\n"
            "• /voz `on` / `off` — activa respuestas de voz\n\n"
            "Escribe tu consulta directamente y navegaré el árbol de decisión correspondiente 🌳"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _notebook(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Muestra información de la base de conocimiento RAG."""
        await update.message.chat.send_action(action="typing")

        lines = ["📚 *Base de Conocimiento ImpuestIA*", ""]

        # Contar chunks por tipo
        try:
            from supabase_client import supabase
            tbl = supabase.table("document_chunks")

            # Total de chunks
            result = tbl.select("*", count="exact").limit(0).execute()
            total = result.count or 0
            lines.append(f"📄 *Documentos indexados:* {total} chunks")

            # Por tipo
            for source_type in ["ley", "circular", "jurisprudencia_judicial", "oficio", "resolucion"]:
                try:
                    r = tbl.select("*", count="exact").eq("source_type", source_type).limit(0).execute()
                    count = r.count or 0
                    if count > 0:
                        emoji = {"ley": "⚖️", "circular": "📋", "jurisprudencia_judicial": "🏛️",
                                 "oficio": "📨", "resolucion": "📜"}.get(source_type, "📄")
                        lines.append(f"  {emoji} {source_type.replace('_', ' ').title()}: {count}")
                except Exception:
                    pass

        except Exception as e:
            lines.append(f"⚠️ No se pudo consultar la base: `{str(e)[:100]}`")

        lines.extend([
            "",
            "💡 Escribe cualquier consulta tributaria y buscaré en estas fuentes.",
        ])

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _manual(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await update.message.reply_text(
            "📝 El modo escritor (manual / artículo / guion) fue descontinuado.\n\n"
            "Ahora ClaudIA opera con *árboles de decisión jurídica* para máxima precisión.\n\n"
            "Escribe tu consulta directamente y navegaré el árbol correspondiente. 🌳"
        )

    async def _articulo(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._manual(update, context)

    async def _guion(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._manual(update, context)

    async def _historia(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._manual(update, context)

    async def _outline(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._manual(update, context)

    async def _voz(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = int(update.effective_chat.id)
        arg = (context.args[0] if context.args else "").strip().lower()

        if arg in ("on", "1", "true", "si", "sí"):
            self._sessions.setdefault(chat_id, {})["voice_enabled"] = True
            await update.message.reply_text("🎙️ Modo voz *ACTIVADO*.")
        elif arg in ("off", "0", "false", "no"):
            self._sessions.setdefault(chat_id, {})["voice_enabled"] = False
            await update.message.reply_text("📝 Modo voz *DESACTIVADO*.")
        else:
            enabled = self._sessions.get(chat_id, {}).get("voice_enabled", False)
            status = "ACTIVADO" if enabled else "DESACTIVADO"
            await update.message.reply_text(
                f"🎙️ Modo voz está *{status}*.\n\n"
                "Usa /voz on  para activar\n"
                "Usa /voz off para desactivar",
                parse_mode="Markdown",
            )

    # ── Procesamiento Outline (modo índice primero) ───────────

    async def _process_outline(
        self,
        update: Update,
        topic: str,
    ) -> None:
        chat_id = int(update.effective_chat.id)
        detected = self.writer.detect_content_type(topic)

        # Research
        await update.message.chat.send_action(action="typing")
        await update.message.reply_text(
            f"🔍 Investigando *{topic}* en la base de conocimiento...",
            parse_mode="Markdown",
        )
        try:
            research = await self.writer.research(topic, detected)
        except Exception as e:
            console.print(f"[red]Research error: {e}[/red]")
            research = ""

        # Outline
        await update.message.chat.send_action(action="typing")
        status_msg = await update.message.reply_text("📝 Generando índice detallado...")

        try:
            outline = await self.writer.generate_outline(topic, research, detected)
        except Exception as e:
            console.print(f"[red]Outline error: {e}[/red]")
            await status_msg.edit_text("❌ Error generando el índice.")
            return

        await status_msg.delete()

        # Guardar sesión para escritura posterior
        self._sessions[chat_id] = {
            "title": topic,
            "content": "",
            "type": detected,
            "outline": outline,
            "research": research,
            "voice_enabled": self._sessions.get(chat_id, {}).get("voice_enabled", False),
            "outline_pending": True,
        }

        # Enviar outline
        chunks = self.writer.split_for_telegram(outline)
        for chunk in chunks:
            await update.message.reply_text(chunk)

        # Instrucción para continuar
        await update.message.reply_text(
            "✅ Este es el índice propuesto.\n\n"
            "Si te gusta, responde con la palabra *escribir* "
            "y generaré el contenido completo.\n"
            "Si quieres cambios, escríbemelos y regeneraré el índice.",
            parse_mode="Markdown",
        )

    # ── Procesamiento central ─────────────────────────────────

    async def _process_request(
        self,
        update: Update,
        topic: str,
        content_type: Optional[str] = None,
        outline: str = "",
        research: str = "",
    ) -> None:
        chat_id = int(update.effective_chat.id)
        detected = content_type or self.writer.detect_content_type(topic)

        # 1. Research (si no viene pre-calculado)
        if not research:
            await update.message.chat.send_action(action="typing")
            status_research = await update.message.reply_text(
                f"🔍 Buscando fuentes legales sobre *{topic}*...",
                parse_mode="Markdown",
            )
            try:
                research = await self.writer.research(topic, detected)
                await status_research.delete()
            except Exception as e:
                console.print(f"[red]Research error: {e}[/red]")
                await status_research.edit_text(
                    "⚠️ No pude consultar la base de conocimiento. Escribiré con el conocimiento general."
                )
                research = ""

        # 2. Write
        await update.message.chat.send_action(action="typing")
        status_msg = await update.message.reply_text(
            f"✍️ Escribiendo tu *{detected}*... esto puede tardar unos segundos.",
            parse_mode="Markdown",
        )

        try:
            content = await self.writer.write(topic, research, detected, outline)
        except Exception as e:
            console.print(f"[red]Write error: {e}[/red]")
            await status_msg.edit_text(
                "❌ Ocurrió un error escribiendo el contenido. Intenta de nuevo."
            )
            return

        # Guardar sesión
        self._sessions[chat_id] = {
            "title": topic,
            "content": content,
            "type": detected,
            "outline": outline,
            "research": research,
            "voice_enabled": self._sessions.get(chat_id, {}).get("voice_enabled", False),
            "outline_pending": False,
        }

        await status_msg.delete()

        # 3. Enviar texto partido
        chunks = self.writer.split_for_telegram(content)
        for chunk in chunks:
            await update.message.reply_text(chunk)

        # 4. Guardar en DB para persistencia
        self._store.save(chat_id, topic, content, detected)

        # 5. Enviar archivos adjuntos automáticamente
        await self._send_exports(update, topic, content, detected)

        # 6. Voz si está activada
        if self._sessions[chat_id].get("voice_enabled") and self.voice:
            await update.message.chat.send_action(action="upload_voice")
            try:
                voice_bytes = await self.voice.synthesize(content[:3800])
                await update.message.reply_voice(
                    voice=voice_bytes,
                    caption="🎙️ Resumen de voz",
                )
            except Exception as e:
                console.print(f"[yellow]TTS falló: {e}[/yellow]")

    # ── Handlers de mensajes ──────────────────────────────────

    async def _handle_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        text_override: Optional[str] = None,
    ) -> None:
        if not update.message:
            return
        text = (text_override or update.message.text or "").strip()
        if not text:
            return

        chat_id = int(update.effective_chat.id)
        session = self._sessions.get(chat_id, {})

        # Si hay un outline pendiente y el usuario dice "escribir"
        if session.get("outline_pending") and text.lower() in ("escribir", "sí", "si", "yes", "ok", "dale"):
            await self._process_request(
                update,
                session["title"],
                session["type"],
                outline=session.get("outline", ""),
                research=session.get("research", ""),
            )
            return

        # Si hay un outline pendiente y el usuario pide cambios
        if session.get("outline_pending"):
            await update.message.reply_text(
                "📝 Regenerando el índice con tus cambios..."
            )
            await self._process_outline(update, text)
            return

        # Si hay un árbol de decisión pendiente, continuarlo
        if session.get("type") == "decision_tree_pending":
            await self._continue_decision_tree(update, text, session)
            return

        # Mensaje libre normal
        detected = self.writer.detect_content_type(text)
        if detected in ("manual", "articulo", "guion", "historia"):
            await self._process_request(update, text, detected)
        else:
            # Por defecto: modo chat conversacional usando NotebookLM directo
            await self._process_chat(update, text)

    @staticmethod
    def _clean_notebooklm_refs(text: str) -> str:
        """Elimina referencias numéricas tipo [1], [2,3] de respuestas de NotebookLM."""
        cleaned = re.sub(r'\[\d+(?:[,‑-]\d+)*\]', '', text)
        cleaned = re.sub(r' +', ' ', cleaned)
        cleaned = re.sub(r'\n\s*\n+', '\n\n', cleaned)
        return cleaned.strip()

    async def _continue_decision_tree(
        self,
        update: Update,
        text: str,
        session: dict,
    ) -> None:
        """Continúa un árbol de decisión donde el usuario se quedó."""
        chat_id = int(update.effective_chat.id)
        tree_id = session["tree_id"]
        current_node_id = session["current_node"]
        facts = dict(session.get("facts", {}))
        
        tree = decision_engine._trees.get(tree_id)
        if not tree:
            await update.message.reply_text("⚠️ No pude continuar el árbol. Intenta con una nueva consulta.")
            self._sessions.pop(chat_id, None)
            return
        
        await update.message.chat.send_action(action="typing")
        status_msg = await update.message.reply_text("🌳 Continuando árbol...")
        
        # Intentar parsear número de opción
        if text.isdigit():
            current_node = tree.nodes.get(current_node_id)
            if current_node and current_node.branches:
                idx = int(text) - 1
                if 0 <= idx < len(current_node.branches):
                    branch = current_node.branches[idx]
                    facts[branch["condition"]] = True
                else:
                    await status_msg.delete()
                    await update.message.reply_text(
                        f"⚠️ Opción no válida. Elige un número entre 1 y {len(current_node.branches)}."
                    )
                    return
        else:
            # Usar LLM para extraer facts de la respuesta
            try:
                new_facts = await decision_engine.interpret_query(text, tree, self.writer._llm)
                facts.update(new_facts)
            except Exception as e:
                console.print(f"[yellow]Error extrayendo facts: {e}[/yellow]")
        
        # Continuar recorrido
        result, path, advanced = decision_engine.continue_tree(tree, current_node_id, facts)
        
        if result.type == "result":
            content = decision_engine.render_result(tree, result, path, include_diagram=True)
            await status_msg.delete()
            await update.message.reply_text(content)
            self._sessions[chat_id] = {
                "title": session["title"],
                "content": content,
                "type": "decision_tree",
                "tree_id": tree.tree_id,
                "path_nodes": [n.id for n in path],
                "voice_enabled": self._sessions.get(chat_id, {}).get("voice_enabled", False),
            }
            return
        
        # Si no avanzamos, re-preguntar
        if not advanced:
            current_node = tree.nodes.get(current_node_id)
            if current_node and current_node.branches:
                interactive = decision_engine.render_interactive(current_node)
                await status_msg.delete()
                await update.message.reply_text(
                    f"🌳 *{tree.title}*\n\n"
                    f"Necesito más información para continuar:\n\n"
                    f"{interactive}\n\n"
                    "Responde con el número de la opción que corresponda."
                )
                self._sessions[chat_id] = {
                    **session,
                    "facts": facts,
                }
                return
        
        # Avanzamos pero llegamos a otro nodo de decisión
        last_node = path[-1] if path else None
        if last_node and last_node.type == "decision" and last_node.branches:
            interactive = decision_engine.render_interactive(last_node)
            await status_msg.delete()
            await update.message.reply_text(
                f"🌳 *{tree.title}*\n\n"
                f"Siguiente pregunta:\n\n"
                f"{interactive}\n\n"
                "Responde con el número de la opción que corresponda."
            )
            self._sessions[chat_id] = {
                **session,
                "current_node": last_node.id,
                "path_so_far": [n.id for n in path],
                "facts": facts,
            }
            return
        
        # Fallback
        await status_msg.edit_text("🌳 Árbol incompleto. Buscando en fuentes...")
        await self._process_chat(update, text)

    async def _process_chat(
        self,
        update: Update,
        text: str,
    ) -> None:
        """Procesa una conversación de chat:
        1. Busca Árbol de Decisión → si hay, navega y responde con precisión.
        2. Si no hay árbol → fallback a RAG.
        """
        chat_id = int(update.effective_chat.id)
        _log_query(chat_id, text)

        await update.message.chat.send_action(action="typing")
        status_msg = await update.message.reply_text("🌳 Buscando árbol de decisión...")

        content = ""
        source = "decision_tree"
        search_results = []

        try:
            # ── PASO 1: Intentar Árbol de Decisión ─────────────────────────
            tree, result_node, path, facts = await decision_engine.navigate_tree(
                text, llm_client=self.writer._llm
            )

            if tree:
                await status_msg.edit_text(f"🌳 Árbol encontrado: *{tree.title}*\nNavegando con LLM...")

                # Si llegamos a un nodo resultado → renderizar
                if result_node and result_node.type == "result":
                    content = decision_engine.render_result(
                        tree, result_node, path, include_diagram=True
                    )
                    await status_msg.delete()
                    await update.message.reply_text(content)
                    source = "decision_tree"

                    # Guardar sesión
                    self._sessions[chat_id] = {
                        "title": text,
                        "content": content,
                        "type": "decision_tree",
                        "tree_id": tree.tree_id,
                        "path_nodes": [n.id for n in path],
                        "voice_enabled": self._sessions.get(chat_id, {}).get("voice_enabled", False),
                    }
                    return

                # Si NO llegamos a resultado → hacer pregunta de clarificación
                # Buscamos el primer nodo de decisión en el path que no tenga match
                if path:
                    last_node = path[-1]
                    if last_node.type == "decision" and last_node.branches:
                        await status_msg.delete()
                        interactive = decision_engine.render_interactive(last_node)
                        await update.message.reply_text(
                            f"🌳 *{tree.title}*\n\n"
                            f"Encontré el árbol, pero necesito más información para llegar a la respuesta:\n\n"
                            f"{interactive}\n\n"
                            "Responde con el número de la opción que corresponda."
                        )
                        # Guardamos estado para continuar conversación
                        self._sessions[chat_id] = {
                            "title": text,
                            "content": "",
                            "type": "decision_tree_pending",
                            "tree_id": tree.tree_id,
                            "current_node": last_node.id,
                            "path_so_far": [n.id for n in path],
                            "facts": facts,
                            "voice_enabled": self._sessions.get(chat_id, {}).get("voice_enabled", False),
                        }
                        return

                # Si no hay branches → algo extraño, fallback a RAG
                await status_msg.edit_text("🌳 Árbol incompleto. Buscando en fuentes...")

            else:
                await status_msg.edit_text("🔍 No hay árbol para este tema. Buscando en fuentes legales...")

            # ── PASO 2: Fallback a RAG ─────────────────────────────────────
            search_results = await rag_engine.search_for_conversation(text)

            # ── PASO 3: Fallback a búsqueda en vivo (live_lookup.py) ────────
            # Se activa si el RAG interno no encontró nada o su mejor
            # resultado está bajo el umbral de confianza (config.RAG_CONFIDENCE_THRESHOLD).
            low_confidence = not search_results or search_results[0].similarity < config.RAG_CONFIDENCE_THRESHOLD
            live_results: list[dict] = []
            if low_confidence:
                await status_msg.edit_text("🌐 Verificando en fuentes oficiales en línea...")
                try:
                    live_results = await live_lookup.search_live(text)
                except Exception as e:
                    console.print(f"[yellow]⚠️ live_lookup falló: {e}[/yellow]")

            if not search_results and not live_results:
                await status_msg.delete()
                content = (
                    "💬 No encontré información sobre eso en mi base de conocimiento tributario.\n\n"
                    "Actualmente tengo árboles de decisión para estos temas del Código Tributario:\n"
                    "• Citación SII (Art. 63)\n"
                    "• Liquidación y giro de oficio (Art. 64-65)\n"
                    "• Determinación de oficio (Art. 59-61)\n"
                    "• Prescripción (Art. 200-201)\n"
                    "• Infracciones y sanciones (Art. 97-98)\n"
                    "• Recurso de reposición (Art. 120-122)\n"
                    "• Intereses por mora (Art. 53-54)\n"
                    "• Cobranza y embargo (Art. 172-177)\n"
                    "• Secreto tributario (Art. 35-37)\n"
                    "• Convenio de pago (Art. 56, 192)\n\n"
                    "Prueba con una consulta relacionada con estos temas."
                )
                source = "rag_empty"
            else:
                context = await rag_engine.build_context(search_results, query=text) if search_results else ""
                live_context = live_lookup.format_for_context(live_results)
                await status_msg.edit_text("💬 Analizando fuentes legales...")

                agent_md = _load_agent_md()

                system = (
                    "Eres ClaudIA, una experta tributaria chilena. Responde en TONO CONVERSACIONAL.\n\n"
                    "REGLA ABSOLUTA: Cada afirmación DEBE ir acompañada de su cita exacta "
                    "entre paréntesis: '(Art. XX del [Cuerpo Legal])' para normas, o "
                    "'(fuente: URL)' si la afirmación viene de una FUENTE WEB EN VIVO. "
                    "NO inventes artículos. Usa SOLO las fuentes proporcionadas.\n\n"
                    "NUNCA uses markdown ni bullets. Máximo 250 palabras.\n\n"
                    "--- PERFIL DEL AGENTE ---\n"
                    f"{agent_md}\n"
                    "--- FIN ---"
                )

                fuentes = context
                if live_context:
                    fuentes = f"{context}\n\n{live_context}" if context else live_context

                user_prompt = (
                    f"Consulta: {text}\n\n"
                    f"FUENTES (usa SOLO esto):\n{fuentes}\n\n"
                    "Responde con precisión y cita la norma o la fuente web. Si no está, di que no tienes esa info."
                )

                content = await self.writer._llm.chat_completion(
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.1,
                    max_tokens=800,
                )
                content = content.strip()

                try:
                    content = guardrail_check(fuentes, content)
                except Exception as e:
                    console.print(f"[yellow]⚠️ Guardrail: {e}[/yellow]")

                source = "rag" if search_results else "live_lookup"

            await status_msg.delete()

            # Guardar sesión
            self._sessions[chat_id] = {
                "title": text,
                "content": content,
                "type": "conversacion",
                "outline": "",
                "research": content,
                "voice_enabled": self._sessions.get(chat_id, {}).get("voice_enabled", False),
                "outline_pending": False,
                "search_results": [r.chunk.chunk_uid for r in search_results],
            }

            # Enviar texto
            await update.message.reply_text(content)

            # PDFs
            if search_results:
                pdf_buttons = []
                seen_pdfs = set()
                for r in search_results[:3]:
                    meta = r.chunk.metadata or {}
                    pdf_url = meta.get("pdf_url", "")
                    if pdf_url and pdf_url != "N/A" and pdf_url not in seen_pdfs:
                        seen_pdfs.add(pdf_url)
                        pdf_buttons.append(
                            InlineKeyboardButton(
                                f"📄 {r.chunk.filename[:30]}",
                                url=pdf_url,
                            )
                        )
                if pdf_buttons:
                    await update.message.reply_text(
                        "📎 Fuentes con PDF:",
                        reply_markup=InlineKeyboardMarkup([pdf_buttons]),
                    )

        except Exception as e:
            console.print(f"[red]Chat error: {e}[/red]")
            import traceback
            console.print(traceback.format_exc())
            await status_msg.edit_text("❌ Error procesando la consulta. Intenta de nuevo.")
            return

        # Voz
        if self._sessions.get(chat_id, {}).get("voice_enabled") and self.voice:
            await update.message.chat.send_action(action="upload_voice")
            try:
                voice_bytes = await self.voice.synthesize(content)
                await update.message.reply_voice(voice=voice_bytes, caption="🎙️ ClaudIA")
            except Exception as e:
                console.print(f"[yellow]TTS: {e}[/yellow]")

    async def _handle_voice(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.message or not update.message.voice:
            return
        if not self.voice:
            await update.message.reply_text(
                "🎙️ El procesamiento de voz no está configurado.\n\n"
                "Asegúrate de tener `OPENAI_API_KEY` configurada en Railway."
            )
            return

        await update.message.chat.send_action(action="typing")
        try:
            voice_file = await update.message.voice.get_file()
            voice_bytes = await voice_file.download_as_bytearray()
            transcript = await self.voice.transcribe(bytes(voice_bytes))
            if not transcript:
                await update.message.reply_text(
                    "🎙️ No pude entender el audio. Intenta hablar más claro o un poco más lento."
                )
                return
            await update.message.reply_text(f'🎙️ Entendí: "{transcript}"')
            await self._process_voice_chat(update, transcript)
        except RuntimeError as e:
            # Errores conocidos de VoiceProcessor (ffmpeg, API no disponible, etc.)
            error_msg = str(e).lower()
            console.print(f"[red]Voice RuntimeError: {e}[/red]")
            if "ffmpeg" in error_msg or "pydub" in error_msg or "normalizar" in error_msg:
                await update.message.reply_text(
                    "🎙️ Error de conversión de audio.\n"
                    "No se encontró `ffmpeg` en el servidor. "
                    "Si eres admin, revisa que esté instalado en el Dockerfile."
                )
            elif "openai" in error_msg or "api key" in error_msg or "authentication" in error_msg:
                await update.message.reply_text(
                    "🎙️ Error con la API de voz (OpenAI).\n"
                    "Revisa que la `OPENAI_API_KEY` sea válida y tenga saldo disponible."
                )
            else:
                await update.message.reply_text(
                    f"🎙️ Error de voz: {e}\n\nIntenta con texto."
                )
        except Exception as e:
            import traceback
            console.print(f"[red]Voice handler error: {e}[/red]")
            console.print(traceback.format_exc())
            await update.message.reply_text(
                "🎙️ Error inesperado procesando el audio.\n"
                f"_Detalle técnico: `{type(e).__name__}`_\n\n"
                "Intenta con texto o contacta al administrador si persiste."
            )

    async def _process_voice_chat(
        self,
        update: Update,
        transcript: str,
    ) -> None:
        """Procesa una conversación por voz: guarda nota → NotebookLM responde → texto + audio."""
        await self._process_chat(update, transcript)

        # Enviar voz adicional siempre que haya voz configurada (modo conversación por voz)
        if self.voice and update.message:
            chat_id = int(update.effective_chat.id)
            session = self._sessions.get(chat_id, {})
            content = session.get("content", "")
            if content:
                await update.message.chat.send_action(action="upload_voice")
                try:
                    voice_bytes = await self.voice.synthesize(content)
                    await update.message.reply_voice(
                        voice=voice_bytes,
                        caption="🎙️ ClaudIA",
                    )
                except Exception as e:
                    console.print(f"[yellow]TTS falló: {e}[/yellow]")
                    await update.message.reply_text(
                        "🎙️ No pude generar el audio, pero ahí va la respuesta en texto."
                    )

    # ── Callbacks (descargas) ─────────────────────────────────

    async def _send_exports(
        self,
        update: Update,
        title: str,
        content: str,
        content_type: str,
    ) -> None:
        """Envía .md y .docx como documentos adjuntos automáticamente."""
        if content_type == "conversacion":
            return

        try:
            md_data = exporter.to_markdown(content, title)
            md_filename = f"{_sanitize_filename(title)}.md"
            await update.message.reply_document(
                document=md_data,
                filename=md_filename,
                caption=f"📄 {content_type} en Markdown",
            )
        except Exception as e:
            console.print(f"[yellow]Error enviando .md: {e}[/yellow]")

        try:
            docx_data = exporter.to_docx(content, title)
            docx_filename = f"{_sanitize_filename(title)}.docx"
            await update.message.reply_document(
                document=docx_data,
                filename=docx_filename,
                caption=f"📝 {content_type} en Word",
            )
        except Exception as e:
            console.print(f"[yellow]Error enviando .docx: {e}[/yellow]")

    async def _handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        chat_id = int(query.message.chat.id)
        session = self._sessions.get(chat_id)

        # Intentar memoria primero, luego SQLite
        if not session or not session.get("content"):
            session = self._store.get_latest(chat_id)

        if not session or not session.get("content"):
            await query.edit_message_text(
                "No encontré contenido reciente. Genera un nuevo documento primero."
            )
            return

        title = session["title"]
        content = session["content"]

        if query.data == "dl_md":
            data = exporter.to_markdown(content, title)
            filename = f"{_sanitize_filename(title)}.md"
        elif query.data == "dl_docx":
            data = exporter.to_docx(content, title)
            filename = f"{_sanitize_filename(title)}.docx"
        else:
            return

        await query.message.reply_document(
            document=data,
            filename=filename,
            caption=f"✅ Aquí tienes tu *{session['type']}*.",
            parse_mode="Markdown",
        )

    # ── Run ───────────────────────────────────────────────────

    def run(self) -> None:
        app = Application.builder().token(self.token).build()
        # Ignorar updates pendientes al reiniciar (evita Conflict con instancias viejas)
        app.drop_pending_updates = True

        app.add_handler(CommandHandler("start", self._start))
        app.add_handler(CommandHandler("fuentes", self._notebook))
        app.add_handler(CommandHandler("manual", self._manual))
        app.add_handler(CommandHandler("articulo", self._articulo))
        app.add_handler(CommandHandler("guion", self._guion))
        app.add_handler(CommandHandler("historia", self._historia))
        app.add_handler(CommandHandler("outline", self._outline))
        app.add_handler(CommandHandler("voz", self._voz))
        app.add_handler(CallbackQueryHandler(self._handle_callback))
        app.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )

        voice_status = "🎙️ voz" if self.voice else "📝 solo texto"
        console.print(
            "[green]✅ Taxpy RAG Bot iniciado[/green]\n"
            f"[dim]RAG: Supabase pgvector[/dim]\n"
            f"[dim]LLM: {config.OPENAI_MODEL} | {voice_status}[/dim]"
        )
        app.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=())
