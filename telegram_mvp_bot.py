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
from rag_engine import rag as rag_engine
from settings_store import store as settings_store
from voice_processor import VoiceProcessor
from writer import WriterEngine

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
            "🤖 *Taxpy — Asistente Tributario*\n\n"
            "Respondo consultas de derecho tributario chileno con sustento legal "
            "usando leyes, circulares y jurisprudencia del SII.\n\n"
            "*Comandos:*\n"
            "• /manual `<tema>` — manual completo con capítulos\n"
            "• /articulo `<tema>` — artículo editorial largo\n"
            "• /guion `<tema>` — guion de video con escenas y planos\n"
            "• /historia `<tema>` — historia narrada como monólogo con sustento legal\n"
            "• /outline `<tema>` — índice detallado primero (tú apruebas)\n"
            "• /fuentes — info de la base de conocimiento\n"
            "• /voz `on` / `off` — activa respuestas de voz\n\n"
            "También puedes escribirme directamente o mandarme un *audio* para hablar con ClaudIA 🎙️"
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
        topic = " ".join(context.args or []).strip()
        if not topic:
            await update.message.reply_text("Usa: /manual `<tema a desarrollar>`")
            return
        await self._process_request(update, topic, "manual")

    async def _articulo(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        topic = " ".join(context.args or []).strip()
        if not topic:
            await update.message.reply_text("Usa: /articulo `<tema a desarrollar>`")
            return
        await self._process_request(update, topic, "articulo")

    async def _guion(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        topic = " ".join(context.args or []).strip()
        if not topic:
            await update.message.reply_text("Usa: /guion `<tema a desarrollar>`")
            return
        await self._process_request(update, topic, "guion")

    async def _historia(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        topic = " ".join(context.args or []).strip()
        if not topic:
            await update.message.reply_text("Usa: /historia `<tema a desarrollar>`")
            return
        await self._process_request(update, topic, "historia")

    async def _outline(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        topic = " ".join(context.args or []).strip()
        if not topic:
            await update.message.reply_text("Usa: /outline `<tema a desarrollar>`")
            return
        await self._process_outline(update, topic)

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

    async def _process_chat(
        self,
        update: Update,
        text: str,
    ) -> None:
        """Procesa una conversación de chat: busca en RAG → responde con GPT-4o."""
        chat_id = int(update.effective_chat.id)

        await update.message.chat.send_action(action="typing")
        status_msg = await update.message.reply_text("🔍 Buscando en la base de conocimiento...")

        content = ""
        source = "rag"
        search_results = []

        try:
            # 1. Buscar en RAG
            search_results = await rag_engine.search_for_conversation(text)

            if not search_results:
                await status_msg.edit_text(
                    "💬 No encontré fuentes específicas sobre eso. Te respondo con conocimiento general..."
                )
                # Fallback a GPT-4o sin contexto
                content = await self.writer.write(text, "", "conversacion")
                source = "gpt4o_fallback"
            else:
                # 2. Construir contexto y generar respuesta
                context = await rag_engine.build_context(search_results)

                await status_msg.edit_text("💬 Analizando fuentes legales...")

                system = (
                    "Eres ClaudIA, una experta tributaria chilena. Responde en TONO CONVERSACIONAL, "
                    "como si estuvieras hablando por teléfono con un colega. "
                    "Usa las fuentes proporcionadas para sustentar tu respuesta. "
                    "NUNCA uses markdown, títulos, bullets ni numeración. "
                    "Máximo 250 palabras. Termina con una pregunta breve."
                )

                user_prompt = (
                    f"Consulta del usuario: {text}\n\n"
                    f"Fuentes relevantes:\n{context}\n\n"
                    "Responde de forma conversacional, citando las normas de manera natural."
                )

                response = await self.writer._openai.chat.completions.create(
                    model=config.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.7,
                    max_tokens=800,
                )
                content = (response.choices[0].message.content or "").strip()

        except Exception as e:
            console.print(f"[red]RAG chat error: {e}[/red]")
            import traceback
            console.print(traceback.format_exc())

            # Fallback: usar GPT-4o
            await status_msg.edit_text(
                "⚠️ No pude consultar la base de conocimiento. Generando respuesta con GPT-4o..."
            )
            try:
                content = await self.writer.write(text, "", "conversacion")
                source = "gpt4o_fallback"
            except Exception as e2:
                console.print(f"[red]Fallback GPT-4o también falló: {e2}[/red]")
                await status_msg.edit_text(
                    "❌ Error generando la respuesta. Intenta de nuevo en unos segundos."
                )
                return

        await status_msg.delete()

        # Guardar sesión ligera
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

        # Botón de PDF si hay fuentes con PDF disponible
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
                    "📎 Fuentes con PDF disponible:",
                    reply_markup=InlineKeyboardMarkup([pdf_buttons]),
                )

        # Voz si está activada (modo legacy /voz on)
        if self._sessions[chat_id].get("voice_enabled") and self.voice:
            await update.message.chat.send_action(action="upload_voice")
            try:
                voice_bytes = await self.voice.synthesize(content)
                await update.message.reply_voice(
                    voice=voice_bytes,
                    caption="🎙️ ClaudIA",
                )
            except Exception as e:
                console.print(f"[yellow]TTS falló: {e}[/yellow]")

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
