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
from pathlib import Path
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
import sqlite3
from datetime import datetime
from context_rag import build_for_chat, law_loader
from article_index import article_index
from citation_guardrail import guardrail_check
from settings_store import store as settings_store
from voice_processor import VoiceProcessor
from decision_engine import engine as decision_engine
from writer import WriterEngine, _load_agent_md
from cowork_manager import (
    crear_trabajo, completar_trabajo, list_trabajos, procesar_entrada_cliente,
    redactar_para_cliente, get_cliente_estado, detectar_tipo_trabajo,
    escanear_entrada, list_clientes as cowork_list_clientes,
)
from obsidian_writer import init_cliente_structure, write_peticion, write_analisis, write_note
from research_agent import run_research
from study_agent import run_study
from ebook_writer import write_ebook


def _log_query(chat_id: int, text: str) -> None:
    """Registra la consulta en SQLite local (best-effort)."""
    try:
        db_path = config.TELEGRAM_DB_PATH
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS query_log (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, query_text TEXT, created_at TEXT)",
            )
            conn.execute(
                "INSERT INTO query_log (chat_id, query_text, created_at) VALUES (?, ?, ?)",
                (chat_id, text[:2000], datetime.utcnow().isoformat()),
            )
    except Exception:
        pass


async def _safe_edit(msg, text: str) -> None:
    """edit_text que nunca revienta el flujo -- Telegram puede devolver
    'Message to edit not found' (el mensaje de estado ya no existe) sin
    que eso deba impedir que la respuesta final igual se envíe."""
    try:
        await msg.edit_text(text)
    except Exception as e:
        console.print(f"[dim]Status edit omitido: {e}[/dim]")


async def _safe_delete(msg) -> None:
    try:
        await msg.delete()
    except Exception as e:
        console.print(f"[dim]Status delete omitido: {e}[/dim]")

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
            "• /voz `on` / `off` — activa respuestas de voz\n"
            "• /cliente listar — ver clientes\n"
            "• /cliente crear NOMBRE — crear cliente\n"
            "• /procesar NOMBRE — procesar docs del cliente\n"
            "• /redactar CLIENTE TIPO INSTRUCCIONES — redactar documento\n"
            "• /investigar [CLIENTE] TEXTO — buscar en fuentes oficiales\n"
            "• /ebook TEMA — generar ebook tributario completo\n\n"
            "Escribe tu consulta directamente y navegaré el árbol de decisión correspondiente 🌳"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _notebook(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Muestra información de la base de conocimiento (context_rag local)."""
        await update.message.chat.send_action(action="typing")

        lines = ["📚 *Base de Conocimiento ImpuestIA*", ""]

        try:
            from context_rag.law_loader import law_loader
            laws = law_loader.all()
            for law in laws:
                lines.append(f"⚖️ *{law.name}*")
                lines.append(f"   {len(law._index)} artículos indexados (~{law.token_estimate:,} tokens)")
            lines.append("")
            # Notas aprobadas en el vault
            try:
                from article_index import article_index
                from obsidian_writer import list_clientes
                article_index.rebuild()
                stats = article_index.stats()
                lines.append(f"📝 Documentos del vault indexados: {stats['docs']}")
                aprobadas = article_index.count_aprobadas()
                lines.append(f"✅ Notas aprobadas: {aprobadas}")
                lines.append(f"👥 Clientes: {len(list_clientes())}")
            except Exception as e:
                lines.append(f"ℹ️ Vault: {str(e)[:80]}")
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

    # ── Comandos Co-Work (Fase 4) ─────────────────────────────

    async def _cliente_cmd(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        args = context.args or []
        subcmd = args[0].lower() if args else "listar"

        if subcmd == "listar":
            clientes = cowork_list_clientes()
            if not clientes:
                await update.message.reply_text("No hay clientes configurados.\nUsa /cliente crear NOMBRE para agregar uno.")
                return
            lines = ["*Clientes configurados:*"]
            for c in clientes:
                estado = get_cliente_estado(c["nombre"])
                lines.append(f"\n• *{c['nombre']}*")
                if c.get("rut"):
                    lines.append(f"  RUT: {c['rut']}")
                if c.get("regimen"):
                    lines.append(f"  Regimen: {c.get('regimen', '')}")
                lines.append(f"  Trabajos: {estado['trabajos_total']} | Pendientes entrada: {estado['archivos_pendientes_entrada']}")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        elif subcmd == "crear" and len(args) >= 2:
            nombre = " ".join(args[1:])
            try:
                init_cliente_structure(nombre)
                await update.message.reply_text(
                    f"Cliente *{nombre}* creado.\n"
                    f"Carpeta: `Clientes/{nombre}/`\n\n"
                    f"Deja documentos en `{nombre}/entrada/` y usa /procesar {nombre} para procesarlos.",
                    parse_mode="Markdown",
                )
            except Exception as e:
                await update.message.reply_text(f"Error: {e}")

        elif subcmd == "estado" and len(args) >= 2:
            nombre = " ".join(args[1:])
            estado = get_cliente_estado(nombre)
            lines = [
                f"*Estado de {nombre}*",
                f"Trabajos totales: {estado['trabajos_total']}",
                f"  Completados: {estado['trabajos_completados']}",
                f"  Pendientes: {estado['trabajos_pendientes']}",
                f"  Errores: {estado['trabajos_error']}",
                f"Archivos en entrada: {estado['archivos_pendientes_entrada']}",
            ]
            if estado["ultimos_trabajos"]:
                lines.append("\n*Ultimos trabajos:*")
                for t in estado["ultimos_trabajos"][:5]:
                    icon = {"completado": "✅", "pendiente": "⏳", "error": "❌"}.get(t["estado"], "•")
                    lines.append(f"  {icon} {t['titulo'][:60]}")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        else:
            await update.message.reply_text(
                "*Comandos de cliente:*\n"
                "/cliente listar — Ver todos los clientes\n"
                "/cliente crear NOMBRE — Crear nuevo cliente\n"
                "/cliente estado NOMBRE — Ver estado de trabajos\n"
                "/procesar NOMBRE — Procesar docs en entrada/\n"
                "/redactar NOMBRE TIPO — Redactar documento para cliente",
                parse_mode="Markdown",
            )

    async def _procesar_cmd(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text("Uso: /procesar NOMBRE_CLIENTE")
            return
        cliente = " ".join(args)
        archivos = escanear_entrada(cliente)
        if not archivos:
            await update.message.reply_text(f"No hay documentos en `{cliente}/entrada/`.")
            return

        await update.message.chat.send_action(action="typing")
        status = await update.message.reply_text(f"Procesando {len(archivos)} documento(s) para *{cliente}*...", parse_mode="Markdown")

        try:
            trabajos = procesar_entrada_cliente(cliente, llm_client=self.writer._llm)
        except Exception as e:
            await status.edit_text(f"Error: {e}")
            return

        lines = [f"*Procesamiento completado para {cliente}*"]
        for t in trabajos:
            icon = {"completado": "✅", "error": "❌"}.get(t.estado, "•")
            lines.append(f"{icon} {t.tipo}: {t.titulo[:50]}")
            if t.error_msg:
                lines.append(f"  _Error: {t.error_msg[:80]}_")
        await status.edit_text("\n".join(lines), parse_mode="Markdown")

    async def _redactar_cmd(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        args = context.args or []
        if len(args) < 1:
            await update.message.reply_text(
                "Uso: /redactar CLIENTE TIPO INSTRUCCIONES\n"
                "Ejemplo: /redactar Nano_Calderon peticion Respuesta a observacion G113 por art 17 LIR",
                parse_mode="Markdown",
            )
            return

        cliente = args[0]
        tipo = args[1] if len(args) > 1 else "peticion"
        instrucciones = " ".join(args[2:]) if len(args) > 2 else ""

        if not instrucciones:
            await update.message.reply_text("Escribe las instrucciones despues del tipo.\nEjemplo: /redactar Nano_Calderon peticion Respuesta a observacion G113")
            return

        await update.message.chat.send_action(action="typing")
        status = await update.message.reply_text(f"Redactando *{tipo}* para *{cliente}*...", parse_mode="Markdown")

        try:
            resultado = redactar_para_cliente(
                cliente=cliente,
                tipo=tipo,
                instrucciones=instrucciones,
                llm_client=self.writer._llm,
            )
        except Exception as e:
            await status.edit_text(f"Error: {e}")
            return

        if resultado.startswith("[ERROR"):
            await status.edit_text(f"Error del LLM: {resultado}")
            return

        t = crear_trabajo(cliente=cliente, tipo=tipo, titulo=instrucciones[:80])

        try:
            if tipo == "peticion":
                output_path = write_peticion(cliente, resultado, titulo=instrucciones[:50])
            elif tipo == "analisis":
                output_path = write_analisis(cliente, resultado, titulo=instrucciones[:50])
            else:
                output_path = write_note(
                    folder=f"Clientes/{cliente}/Notas",
                    filename=f"redaccion_{t.id}",
                    content=resultado,
                    titulo=instrucciones[:50],
                    tipo=tipo,
                    cliente=cliente,
                )
            completar_trabajo(t, contenido=resultado[:500], archivo_salida=str(output_path))
        except Exception:
            pass

        await status.delete()

        chunks = self.writer.split_for_telegram(resultado[:3800])
        for chunk in chunks:
            await update.message.reply_text(chunk)

        md_data = exporter.to_markdown(resultado, f"{tipo}_{cliente}")
        await update.message.reply_document(
            document=md_data,
            filename=f"{tipo}_{cliente}.md",
            caption=f"Redaccion guardada en vault: {cliente}/{tipo.capitalize()}s/",
        )

    # ── Comando Ebook (Fase 6) ──────────────────────────────────

    async def _ebook_cmd(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "*Comando /ebook*\n"
                "Genera un ebook tributario completo con investigacion, indice, "
                "capitulos redactados y exportacion a Markdown + DOCX.\n\n"
                "Uso: /ebook TEMA DEL LIBRO\n\n"
                "Ejemplos:\n"
                "/ebook Guia practica del regimen Pro Pyme art 14 D LIR\n"
                "/ebook Como responder una citacion del SII paso a paso\n"
                "/ebook Todo sobre la prescripcion tributaria en Chile\n\n"
                "_El proceso toma unos minutos. El agente genera el indice, "
                "luego redacta cada capitulo con ejemplos y citas legales._",
                parse_mode="Markdown",
            )
            return

        tema = " ".join(args)
        chat_id = int(update.effective_chat.id)

        status = await update.message.reply_text(f"📚 Generando ebook: *{tema[:80]}*...\n\n🔍 Fase 1/3: Investigando y creando indice...")

        async def report_progress(current, total, msg):
            try:
                pct = f"{current}/{total}" if total > 0 else "..."
                await status.edit_text(
                    f"📚 *{tema[:60]}*\n\n"
                    f"{msg}\n"
                    f"Progreso: {pct}",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        try:
            result = await write_ebook(
                tema=tema,
                llm_client=self.writer._llm,
                progress_callback=report_progress,
            )
        except Exception as e:
            await status.edit_text(f"Error generando el ebook: {e}")
            return

        await status.delete()

        md_path = result["archivos"].get("markdown", "")
        docx_path = result["archivos"].get("docx", "")
        vault_path = result["archivos"].get("vault", "")

        await update.message.reply_text(
            f"✅ *Ebook completado: {result['titulo']}*\n"
            f"📖 {result['capitulos']} capitulos redactados\n\n"
            f"Formatos disponibles:",
            parse_mode="Markdown",
        )

        if md_path:
            md_file = Path(md_path)
            if md_file.exists():
                await update.message.reply_document(
                    document=open(md_path, "rb"),
                    filename=md_file.name,
                    caption=f"📄 {result['titulo']} — Markdown",
                )

        if docx_path:
            docx_file = Path(docx_path)
            if docx_file.exists():
                await update.message.reply_document(
                    document=open(docx_path, "rb"),
                    filename=docx_file.name,
                    caption=f"📝 {result['titulo']} — Word",
                )

    # ── Comando Investigar (Fase 5) ────────────────────────────

    async def _investigar_cmd(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "*Comando /investigar*\n"
                "Busca jurisprudencia, circulares y doctrina en fuentes oficiales (bcn.cl, sii.cl) "
                "y guarda los resultados en tu vault de Obsidian.\n\n"
                "Uso:\n"
                "/investigar CLIENTE TEXTO — busca para un cliente especifico\n"
                "/investigar TEXTO — busca en general\n\n"
                "Ejemplos:\n"
                "/investigar Nano_Calderon prescripcion art 200 CT\n"
                "/investigar jurisprudencia art 17 n8 LIR inmuebles\n"
                "/investigar circulares SII regimen pro pyme 2025",
                parse_mode="Markdown",
            )
            return

        first = args[0]
        clientes_registrados = [c["nombre"] for c in cowork_list_clientes()]
        cliente = None
        query_start = 0

        if first in clientes_registrados:
            cliente = first
            query_start = 1

        if query_start >= len(args):
            await update.message.reply_text("Escribe el texto a investigar despues del nombre del cliente.\nEjemplo: /investigar prescripcion art 200 CT")
            return

        query = " ".join(args[query_start:])
        await update.message.chat.send_action(action="typing")
        status = await update.message.reply_text(f"🔍 Investigando: _{query}_...")

        try:
            result = await run_research(query, cliente=cliente, max_results=3)
        except Exception as e:
            await status.edit_text(f"Error en la investigacion: {e}")
            return

        await status.delete()

        reply = result.get("reply", "Sin resultados.")
        await update.message.reply_text(reply, parse_mode="Markdown", disable_web_page_preview=True)

        if result["resultados"] > 0:
            vault_note = (
                f"\n_Investigacion guardada en Obsidian:_\n"
                f"`{result['resumen']}`\n"
                f"_Archivos: {len(result['archivos'])}_"
            )
            await update.message.reply_text(vault_note, parse_mode="Markdown")

    # ── Comando Estudio (modo documento largo) ─────────────────

    async def _estudio_cmd(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "*Comando /estudio*\n"
                "Genera un estudio tributario completo y documentado: ley completa + "
                "jurisprudencia + tus notas del vault + busqueda en vivo. "
                "El resultado se guarda en Obsidian y se envia como archivo .md.\n\n"
                "Uso:\n"
                "/estudio CLIENTE TEMA — estudio para un cliente especifico\n"
                "/estudio TEMA — estudio general\n\n"
                "Ejemplos:\n"
                "/estudio venta de inmuebles persona natural art 17 n 8\n"
                "/estudio Nano_Calderon prescripcion de impuestos art 200 CT",
                parse_mode="Markdown",
            )
            return

        first = args[0]
        clientes_registrados = [c["nombre"] for c in cowork_list_clientes()]
        cliente = None
        query_start = 0
        if first in clientes_registrados:
            cliente = first
            query_start = 1

        if query_start >= len(args):
            await update.message.reply_text(
                "Escribe el tema del estudio despues del nombre del cliente.\n"
                "Ejemplo: /estudio prescripcion art 200 CT"
            )
            return

        topic = " ".join(args[query_start:])
        await update.message.chat.send_action(action="typing")
        status = await update.message.reply_text(
            f"📚 Generando estudio: _{topic}_...\n"
            "(ley completa + jurisprudencia + notas + web; puede tardar 1-2 min)",
            parse_mode="Markdown",
        )

        try:
            result = await run_study(topic, cliente=cliente, llm_client=self.writer._llm)
        except Exception as e:
            console.print(f"[red]Estudio error: {e}[/red]")
            await _safe_edit(status, f"❌ Error generando el estudio: {e}")
            return

        await _safe_delete(status)

        # Resumen corto en el chat
        laws = ", ".join(result.get("laws_loaded", []))
        docs = len(result.get("docs_usados", []))
        live_n = len(result.get("live_usado", []))
        refs = result.get("refs", [])
        refs_txt = ", ".join(f"Art. {a} {t.upper()}" for t, a in refs[:6])
        summary = (
            f"✅ *Estudio listo:* {topic[:80]}\n\n"
            f"Leyes: {laws or '—'} | Docs del vault: {docs} | Fuentes web: {live_n}\n"
            + (f"Citas detectadas: {refs_txt}\n" if refs_txt else "")
            + f"Guardado en Obsidian: `{result['path']}`"
        )
        await update.message.reply_text(summary, parse_mode="Markdown")

        # Enviar el .md como documento
        try:
            md_path = Path(result["path"])
            with open(md_path, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=md_path.name,
                    caption=f"📄 Estudio: {topic[:100]}",
                )
        except Exception as e:
            console.print(f"[yellow]No se pudo enviar el archivo: {e}[/yellow]")

        # Guardar sesion
        chat_id = int(update.effective_chat.id)
        self._sessions[chat_id] = {
            "title": topic,
            "content": result["content"],
            "type": "estudio",
            "voice_enabled": self._sessions.get(chat_id, {}).get("voice_enabled", False),
        }

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
            await _safe_edit(status_msg, "❌ Error generando el índice.")
            return

        await _safe_delete(status_msg)

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
            await _safe_edit(status_msg, 
                "❌ Ocurrió un error escribiendo el contenido. Intenta de nuevo."
            )
            return

        # Mismo guardrail anti-alucinación que usa _process_chat -- este modo ya
        # no se auto-dispara desde texto libre, pero si se reactiva explícitamente
        # más adelante no debe repetir el incidente de citas inventadas.
        try:
            content = guardrail_check(research, content)
        except Exception as e:
            console.print(f"[yellow]⚠️ Guardrail: {e}[/yellow]")

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

        await _safe_delete(status_msg)

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

        # Mensaje libre normal: consultor por defecto (árbol de decisión -> RAG ->
        # búsqueda en vivo). El modo escritor (manual/articulo/guion/historia) ya
        # no se auto-dispara desde texto libre -- /manual, /articulo, etc. avisan
        # que está descontinuado (ver _manual arriba); writer.write() sigue
        # disponible para reactivarlo explícitamente más adelante.
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
                    await _safe_delete(status_msg)
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
            await _safe_delete(status_msg)
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
                await _safe_delete(status_msg)
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
            await _safe_delete(status_msg)
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
        await _safe_edit(status_msg, "🌳 Árbol incompleto. Buscando en fuentes...")
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
                await _safe_edit(status_msg, f"🌳 Árbol encontrado: *{tree.title}*\nNavegando con LLM...")

                # Si llegamos a un nodo resultado → renderizar
                if result_node and result_node.type == "result":
                    content = decision_engine.render_result(
                        tree, result_node, path, include_diagram=True
                    )
                    await _safe_delete(status_msg)
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
                        await _safe_delete(status_msg)
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
                await _safe_edit(status_msg, "🌳 Árbol incompleto. Buscando en fuentes...")

            else:
                await _safe_edit(status_msg, "🔍 No hay árbol para este tema. Cargando leyes completas...")

            # ── PASO 2: Context-RAG (leyes completas, sin chunking) ────────
            await _safe_edit(status_msg, "📚 Cargando textos legales completos...")

            # Notas aprobadas (conocimiento validado por el usuario) — se cargan
            # antes del prompt principal para que las leyes NO se recarguen con
            # ellas y para que tengan prioridad sobre la búsqueda web.
            approved_text = ""
            try:
                article_index.rebuild()
                approved_text, _approved_used = article_index.approved_context(
                    text,
                    max_docs=config.APPROVED_NOTES_LIMIT,
                    char_limit=config.APPROVED_NOTE_CHARS,
                )
            except Exception as e:
                console.print(f"[yellow]⚠️ Notas aprobadas no disponibles: {e}[/yellow]")

            if approved_text:
                await _safe_edit(status_msg, "📌 Usando notas aprobadas + texto legal...")

            system, user_prompt, prompt_input = await build_for_chat(
                query=text,
                llm_client=self.writer._llm,
            )

            tags_loaded = prompt_input.route.law_tags
            tokens_est = prompt_input.tokens_used or sum(law_loader.get(t).token_estimate for t in tags_loaded if law_loader.get(t))
            trim_note = " (smart trim)" if prompt_input.trim_applied else ""
            await _safe_edit(status_msg, f"💬 Analizando con {len(tags_loaded)} leyes (~{tokens_est:,} tokens{trim_note})...")

            # ── PASO 3: Enriquecer con búsqueda en vivo (opcional) ────────
            # Las notas aprobadas van ANTES de la web; la web solo refuerza.
            if approved_text:
                user_prompt = f"{approved_text}\n{user_prompt}"
            live_context = ""
            if prompt_input.route.needs_live_search:
                try:
                    live_results = await live_lookup.search_live(text)
                    live_context = live_lookup.format_for_context(live_results) if live_results else ""
                    if live_context:
                        user_prompt = f"{user_prompt}\n\n=== FUENTES EN VIVO (WEB) ===\n{live_context}"
                except Exception as e:
                    console.print(f"[yellow]⚠️ live_lookup falló: {e}[/yellow]")

            content = await self.writer._llm.chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=800,
            )
            content = content.strip()
            source = "context_rag"
            search_results = []

            # Guardrail anti-alucinación: valida que las citas existan en las
            # fuentes efectivamente entregadas (leyes + notas aprobadas).
            try:
                content = guardrail_check(user_prompt, content)
            except Exception as e:
                console.print(f"[yellow]⚠️ Guardrail: {e}[/yellow]")

            await _safe_delete(status_msg)

            # Guardar sesión
            self._sessions[chat_id] = {
                "title": text,
                "content": content,
                "type": "conversacion",
                "outline": "",
                "research": content,
                "voice_enabled": self._sessions.get(chat_id, {}).get("voice_enabled", False),
                "outline_pending": False,
                "laws_loaded": tags_loaded,
            }

            # Enviar texto
            await update.message.reply_text(content)

        except Exception as e:
            console.print(f"[red]Chat error: {e}[/red]")
            import traceback
            console.print(traceback.format_exc())
            await _safe_edit(status_msg, "❌ Error procesando la consulta. Intenta de nuevo.")
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
        app.add_handler(CommandHandler("cliente", self._cliente_cmd))
        app.add_handler(CommandHandler("procesar", self._procesar_cmd))
        app.add_handler(CommandHandler("redactar", self._redactar_cmd))
        app.add_handler(CommandHandler("investigar", self._investigar_cmd))
        app.add_handler(CommandHandler("estudio", self._estudio_cmd))
        app.add_handler(CommandHandler("ebook", self._ebook_cmd))
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
