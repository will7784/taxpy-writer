"""
Bot de Telegram simplificado — Taxpy Writer.

Sin cuotas, sin invites, sin RAG local.
Solo NotebookLM + GPT-4o para escribir manuales, artículos y guiones.
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
        self.voice = VoiceProcessor() if config.GOOGLE_API_KEY else None
        self._store = SessionStore(config.TELEGRAM_DB_PATH)
        # Sesiones en memoria: chat_id -> dict
        self._sessions: dict[int, dict] = {}

    # ── Comandos ──────────────────────────────────────────────

    async def _start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        text = (
            "🤖 *Taxpy Writer*\n\n"
            "Escribo manuales, artículos y guiones de video sobre "
            "derecho tributario chileno usando todo el conocimiento "
            "de mi cuaderno NotebookLM.\n\n"
            "*Comandos:*\n"
            "• /manual `<tema>` — manual completo con capítulos\n"
            "• /articulo `<tema>` — artículo editorial largo\n"
            "• /guion `<tema>` — guion de video con escenas y planos\n"
            "• /outline `<tema>` — índice detallado primero (tú apruebas)\n"
            "• /notebook — info del cuaderno conectado\n"
            "• /voz `on` / `off` — activa respuestas de voz\n\n"
            "También puedes escribirme directamente o mandarme un *audio* 🎙️"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _notebook(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Muestra información de los notebooks conectados con diagnóstico detallado."""
        await update.message.chat.send_action(action="typing")

        # Diagnóstico paso a paso
        auth_raw = config.NOTEBOOKLM_AUTH_JSON
        auth_len = len(auth_raw)
        has_fallback = (config.BASE_DIR / "notebooklm_auth.json").exists()
        lines = ["📓 *Diagnóstico NotebookLM*", ""]

        if auth_len == 0:
            lines.extend(
                [
                    "❌ *NOTEBOOKLM_AUTH_JSON* está vacío.",
                    "",
                    "*Posibles causas:*",
                    "1. No existe el archivo `notebooklm_auth.json`",
                    "2. La variable de entorno no está configurada",
                    "",
                    "*Solución:*",
                    "1. Sube el archivo `storage_state.json` desde el panel web",
                    "2. O agrega la variable `NOTEBOOKLM_AUTH_JSON` en Railway",
                    "",
                    "*Para obtener el JSON en tu PC:*",
                    "```",
                    "Get-Content $env:USERPROFILE\\.notebooklm\\profiles\\default\\storage_state.json -Raw",
                    "```",
                ]
            )
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            return

        lines.append(f"✅ Auth detectada: *{auth_len}* caracteres.")
        if has_fallback:
            lines.append("✅ Usando archivo fallback `notebooklm_auth.json`.")

        # Intentar conectar
        try:
            notebooks = await self.writer.nb_manager.list_notebooks()
            lines.append(f"✅ Conexión exitosa. Cuentas con *{len(notebooks)}* cuaderno(s).")
        except Exception as e:
            error_str = str(e).lower()
            lines.append("❌ Error al conectar con NotebookLM.")
            if "auth" in error_str or "unauthorized" in error_str or "credential" in error_str:
                lines.extend(
                    [
                        "",
                        "*Causa probable:* Las credenciales expiraron o son inválidas.",
                        "",
                        "*Solución:*",
                        "1. Ejecuta `notebooklm login` en tu PC local",
                        "2. Sube el nuevo `storage_state.json` desde el panel web",
                        "3. O actualiza la variable en Railway",
                    ]
                )
            elif "not installed" in error_str or "notebooklm" in error_str:
                lines.append(
                    "*Causa:* La librería `notebooklm-py` no está instalada. "
                    "Verifica `requirements.txt`."
                )
            else:
                lines.append(f"*Detalle:* `{str(e)[:200]}`")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            return

        # Leer configuración actual desde settings
        primary_name = settings_store.get("primary_notebook_name", config.NOTEBOOKLM_NOTEBOOK_NAME)
        secondary_name = settings_store.get("secondary_notebook_name", "")
        primary_id = ""
        secondary_id = ""
        primary_sources = 0

        try:
            primary_id = await self.writer.nb_manager.create_or_get_notebook()
            sources = await self.writer.nb_manager.get_notebook_sources(primary_id)
            primary_sources = len(sources)
        except Exception:
            pass

        lines.extend(
            [
                "",
                f"*🥇 Cuaderno primario:* `{primary_name}`",
                f"*ID:* `{primary_id or 'No encontrado'}`",
                f"*Fuentes:* {primary_sources}",
            ]
        )

        if secondary_name:
            lines.extend(
                [
                    "",
                    f"*🥈 Cuaderno secundario:* `{secondary_name}`",
                    f"*ID:* `{secondary_id or 'No configurado'}`",
                ]
            )

        lines.extend(
            [
                "",
                "*Cuadernos disponibles:*",
            ]
        )
        for nb in notebooks[:15]:
            markers = []
            if nb["id"] == primary_id:
                markers.append("🥇")
            if nb.get("name") == secondary_name:
                markers.append("🥈")
            marker = " " + " ".join(markers) if markers else ""
            lines.append(f"  • `{nb['name']}`{marker}")
        if len(notebooks) > 15:
            lines.append(f"  ... y {len(notebooks) - 15} más")

        lines.append(
            "\n_Gestiona tus cuadernos desde el panel web:_ "
            "https://taxpy-writer-production.up.railway.app/dashboard"
        )

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
            f"🔍 Investigando *{topic}* en NotebookLM...",
            parse_mode="Markdown",
        )
        try:
            research = await self.writer.research(topic)
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
            await update.message.reply_text(
                f"🔍 Investigando *{topic}* en NotebookLM...",
                parse_mode="Markdown",
            )
            try:
                research = await self.writer.research(topic)
            except Exception as e:
                console.print(f"[red]Research error: {e}[/red]")
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
        await self._process_request(update, text, detected)

    async def _handle_voice(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.message or not update.message.voice:
            return
        if not self.voice:
            await update.message.reply_text(
                "🎙️ El procesamiento de voz no está configurado."
            )
            return

        await update.message.chat.send_action(action="typing")
        try:
            voice_file = await update.message.voice.get_file()
            voice_bytes = await voice_file.download_as_bytearray()
            transcript = await self.voice.transcribe(bytes(voice_bytes))
            if not transcript:
                await update.message.reply_text(
                    "🎙️ No pude entender el audio. Intenta hablar más claro."
                )
                return
            await update.message.reply_text(f'🎙️ Entendí: "{transcript}"')
            await self._handle_text(update, context, text_override=transcript)
        except Exception as e:
            console.print(f"[red]Voice handler error: {e}[/red]")
            await update.message.reply_text(
                "Ocurrió un error procesando el audio. Intenta con texto."
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

        app.add_handler(CommandHandler("start", self._start))
        app.add_handler(CommandHandler("notebook", self._notebook))
        app.add_handler(CommandHandler("manual", self._manual))
        app.add_handler(CommandHandler("articulo", self._articulo))
        app.add_handler(CommandHandler("guion", self._guion))
        app.add_handler(CommandHandler("outline", self._outline))
        app.add_handler(CommandHandler("voz", self._voz))
        app.add_handler(CallbackQueryHandler(self._handle_callback))
        app.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )

        voice_status = "🎙️ voz" if self.voice else "📝 solo texto"
        console.print(
            "[green]✅ Taxpy Writer Bot iniciado[/green]\n"
            f"[dim]NotebookLM: {config.NOTEBOOKLM_NOTEBOOK_NAME}[/dim]\n"
            f"[dim]LLM: {config.OPENAI_MODEL} | {voice_status}[/dim]"
        )
        app.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=())
