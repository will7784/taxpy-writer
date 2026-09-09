"""
Configuracion centralizada — ImpuestIA (local context-rag)
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# ============================================
# Telegram
# ============================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ============================================
# OpenAI (escritura + voz)
# ============================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
# Investigación puede usar un proveedor distinto del chat habitual.
RESEARCH_LLM_PROVIDER = os.getenv("RESEARCH_LLM_PROVIDER", "").strip().lower()
# Proveedor LLM por defecto para el chat/agente (kimi | gemini | deepseek | openai | custom).
# 'deepseek' es el más barato y el DEFAULT. Si no está configurado o es inválido, cae a la
# cadena clásica (kimi > gemini > deepseek > openai > custom). Cambia a 'kimi'/'gemini' si
# necesitas los 1M de contexto para leyes completas en el chat general.
DEFAULT_LLM_PROVIDER = os.getenv("DEFAULT_LLM_PROVIDER", "deepseek").strip().lower()

# Google Gemini (recomendado: 1M contexto, leyes completas)
# =================================================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MAX_CONTEXT = int(os.getenv("GEMINI_MAX_CONTEXT", "1000000"))

# Kimi / Moonshot (1M contexto, leyes completas — recomendado para modo estudio)
# API OpenAI-compatible: https://platform.moonshot.ai
# =================================================================
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1")
KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-k2-0905-preview")
KIMI_MAX_CONTEXT = int(os.getenv("KIMI_MAX_CONTEXT", "1000000"))

# DeepSeek (API OpenAI-compatible, base https://api.deepseek.com)
# =================================================================
# Modelos V4: deepseek-v4-flash (barato/rápido, default) y deepseek-v4-pro (complejo).
# Precios variables (hay descuento off-peak); ver https://api-docs.deepseek.com/quick_start/pricing
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_MODEL_PRO = os.getenv("DEEPSEEK_MODEL_PRO", "deepseek-v4-pro")
DEEPSEEK_MAX_CONTEXT = int(os.getenv("DEEPSEEK_MAX_CONTEXT", "128000"))

# Cualquier API OpenAI-compatible (Qwen, Moonshot, Zhipu, etc.)
# =================================================================
CUSTOM_LLM_API_KEY = os.getenv("CUSTOM_LLM_API_KEY", "")
CUSTOM_LLM_BASE_URL = os.getenv("CUSTOM_LLM_BASE_URL", "")
CUSTOM_LLM_MODEL = os.getenv("CUSTOM_LLM_MODEL", "")
CUSTOM_LLM_MAX_CONTEXT = int(os.getenv("CUSTOM_LLM_MAX_CONTEXT", "128000"))

# ============================================
# Notas Aprobadas — conocimiento validado por el usuario
# ============================================
# Notas del vault marcadas como aprobadas (aprobada: true) que el bot
# consulta con prioridad ANTES de recurrir a la búsqueda web.
APPROVED_NOTES_LIMIT = int(os.getenv("APPROVED_NOTES_LIMIT", "3"))
APPROVED_NOTE_CHARS = int(os.getenv("APPROVED_NOTE_CHARS", "4000"))

# ============================================
# Supabase — OPCIONAL / LEGACY
# Ya NO se usa en el runtime (el motor es context_rag). Queda documentado
# por si se reedifica el backend vectorial más adelante.
# ============================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

# ============================================
# Jurisdiccion activa (chile | colombia)
# ============================================
JURISDICCION = os.getenv("JURISDICCION", "chile")

# ============================================
# Capa 3: busqueda en vivo (live_lookup.py)
# ============================================
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
# Ventana máxima admitida por cada consulta de Tavily. ClaudIA ejecuta todas
# sus consultas y analiza todos los documentos pertinentes que el proveedor entregue.
TAVILY_RESULTS_PER_QUERY = int(os.getenv("TAVILY_RESULTS_PER_QUERY", "20"))
# Límite técnico de concurrencia para no saturar los sitios consultados; no
# descarta fuentes ni limita cuántas se investigan.
RESEARCH_FETCH_CONCURRENCY = int(os.getenv("RESEARCH_FETCH_CONCURRENCY", "6"))
# El análisis divide contexto solo por capacidad del modelo y procesa varias
# tandas en paralelo. Ninguna fuente pertinente se omite por ello.
RESEARCH_ANALYSIS_CONCURRENCY = int(os.getenv("RESEARCH_ANALYSIS_CONCURRENCY", "1"))
RESEARCH_EVIDENCE_CHARS_PER_BATCH = int(os.getenv("RESEARCH_EVIDENCE_CHARS_PER_BATCH", "60000"))
# Espacio de salida para hallazgos e informe; no limita fuentes ni citas.
# 8000 mantiene compatibilidad con deepseek-chat. Ajustar al cambiar de modelo.
RESEARCH_MAX_OUTPUT_TOKENS = int(os.getenv("RESEARCH_MAX_OUTPUT_TOKENS", "8000"))
RESEARCH_LLM_TIMEOUT_SECONDS = float(os.getenv("RESEARCH_LLM_TIMEOUT_SECONDS", "240"))
# Presupuesto compartido de la investigación profunda. Se expresa en USD para
# poder aplicar el mismo límite aunque se cambie de proveedor.
RESEARCH_MONTHLY_BUDGET_USD = float(os.getenv("RESEARCH_MONTHLY_BUDGET_USD", "150"))
RESEARCH_RUN_BUDGET_USD = float(os.getenv("RESEARCH_RUN_BUDGET_USD", "15"))
RESEARCH_RESERVED_FINAL_SHARE = float(os.getenv("RESEARCH_RESERVED_FINAL_SHARE", "0.25"))
# La sincronización semanal vuelve a revisar documentos recientes: algunos
# portales publican o corrigen fallos después de su fecha original.
LIBRARY_LOOKBACK_DAYS = int(os.getenv("LIBRARY_LOOKBACK_DAYS", "14"))

# ============================================
# DEPRECATED: vars del RAG con chunks
# ============================================
# OPENAI_EMBEDDING_MODEL  — ya no se usa (context-rag no necesita embeddings)
# RAG_CONFIDENCE_THRESHOLD — ya no se usa (no hay similarity de chunks)
# NOTEBOOKLM_* — ya no se usa
NOTEBOOKLM_NOTEBOOK_NAME = os.getenv("NOTEBOOKLM_NOTEBOOK_NAME", "impuestia-default")

# ============================================
# Mitigacion Lost-in-the-Middle (LitM)
# Reordena el contexto en forma de U para que lo mas relevante quede en los
# extremos (primacia/recencia) y no se pierda en el medio del contexto.
# ============================================
LITM_REORDER = os.getenv("LITM_REORDER", "1") == "1"

# ============================================
# Pre-resumir (compresion con LLM intermedio)
# Cuando el contexto es muy extenso, un paso intermedio destila las leyes a un
# resumen denso (conservando articulos y cifras exactas) antes del prompt final.
# ============================================
PRE_SUMMARIZE = os.getenv("PRE_SUMMARIZE", "1") == "1"
PRE_SUMMARIZE_MIN_TOKENS = int(os.getenv("PRE_SUMMARIZE_MIN_TOKENS", "60000"))

# ============================================
# Writer
# ============================================
WRITER_MAX_TOKENS = int(os.getenv("WRITER_MAX_TOKENS", "8000"))
WRITER_TEMPERATURE = float(os.getenv("WRITER_TEMPERATURE", "0.2"))

# ============================================
# Paths
# ============================================
TELEGRAM_DB_PATH = Path(os.getenv("TELEGRAM_DB_PATH", str(BASE_DIR / "impuestia.sqlite3")))
PRODUCTION_DB_PATH = Path(os.getenv("PRODUCTION_DB_PATH", str(BASE_DIR / "impuestia_production.sqlite3")))
BACKUPS_DIR = Path(os.getenv("BACKUPS_DIR", str(BASE_DIR / "backups")))
EXPORTS_DIR = Path(os.getenv("EXPORTS_DIR", str(BASE_DIR / "exports")))
EXPORTS_DIR.mkdir(exist_ok=True)

DOCUMENTS_DIR = Path(os.getenv("DOCUMENTS_DIR", str(BASE_DIR / "documents")))
KNOWLEDGE_DIR = Path(os.getenv("KNOWLEDGE_DIR", str(BASE_DIR / "knowledge")))
KNOWLEDGE_DIR.mkdir(exist_ok=True)

# ============================================
# Agente de escritura
# ============================================
AGENT_MD_FILE = BASE_DIR / "agent.md"

# ============================================
# Railway / Server (healthcheck + frontend)
# ============================================
API_SERVER_PORT = int(os.getenv("PORT", os.getenv("API_SERVER_PORT", "8000")))

# ============================================
# Obsidian Vault Integration
# ============================================
OBSIDIAN_VAULT_PATH = Path(os.getenv("OBSIDIAN_VAULT_PATH", r"C:\Users\lyf-a\Dropbox\OBSIDIAN\Impuestia"))

# ============================================
# Co-Work por Cliente
# ============================================
COWORK_PATH = Path(os.getenv("COWORK_PATH", str(OBSIDIAN_VAULT_PATH / "Clientes")))
COWORK_PATH.mkdir(parents=True, exist_ok=True)

# ============================================
# Admin panel credentials (fallback seguro)
# ============================================
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")

# Produccion: vigilancia y alertas de fuentes oficiales.
OFFICIAL_SYNC_HOUR = os.getenv("OFFICIAL_SYNC_HOUR", "06:15")
OFFICIAL_SYNC_TIMEZONE = os.getenv("OFFICIAL_SYNC_TIMEZONE", "America/Santiago")
ADMIN_TELEGRAM_CHAT_IDS = [x.strip() for x in os.getenv("ADMIN_TELEGRAM_CHAT_IDS", "").split(",") if x.strip()]
BCN_LEYCHILE_URLS = [x.strip() for x in os.getenv("BCN_LEYCHILE_URLS", "").split(",") if x.strip()]
CONGRESS_SOURCE_URLS = [x.strip() for x in os.getenv("CONGRESS_SOURCE_URLS", "").split(",") if x.strip()]
DIARIO_OFICIAL_URLS = [x.strip() for x in os.getenv("DIARIO_OFICIAL_URLS", "").split(",") if x.strip()]
SII_OFFICIAL_URLS = [x.strip() for x in os.getenv("SII_OFFICIAL_URLS", "").split(",") if x.strip()]


# ============================================
# MCP (Model Context Protocol) — harness externo
# ============================================
# Token bearer para que el harness/agente externo se autentique contra el
# endpoint MCP montado en el panel. Si está vacío, el endpoint queda abierto
# (solo para desarrollo local). En producción SIEMPRE debe estar seteado.
MCP_TOKEN = os.getenv("MCP_TOKEN", "")
# Ruta base del endpoint MCP. El SSE queda en {MCP_MOUNT_PATH}/sse (ej. /mcp/sse).
MCP_MOUNT_PATH = os.getenv("MCP_MOUNT_PATH", "/mcp")

# ============================================
# Ingesta automática (webhook tipo n8n) — /api/ingest/{cliente}
# ============================================
# Token bearer/X-API-Key para que un flujo externo (n8n, Dropbox, Google Drive)
# suba documentos de clientes automáticamente. Vacío = abierto (solo dev).
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")
# Si es 1/true/yes, al recibir un documento se dispara el pipeline de Co-Work
# (OCR + análisis) automáticamente, para que quede consultable.
INGEST_AUTOPROCESS = os.getenv("INGEST_AUTOPROCESS", "1").strip().lower() in {"1", "true", "yes"}


def require_production_secrets() -> None:
    """Evita desplegar el panel con credenciales conocidas o una cookie insegura."""
    missing = [name for name, value in {
        "ADMIN_USERNAME": ADMIN_USERNAME, "ADMIN_PASSWORD": ADMIN_PASSWORD, "SESSION_SECRET": SESSION_SECRET,
    }.items() if not value]
    if missing:
        raise RuntimeError("Configura secretos de producción: " + ", ".join(missing))

# ============================================
# Sync vault producción → Obsidian local
# ============================================
# URL del panel de producción desde donde sync_vault.py descarga el vault.
SYNC_PROD_URL = os.getenv("SYNC_PROD_URL", "https://taxpy-writer-production.up.railway.app")
SYNC_STATE_PATH = Path(os.getenv("SYNC_STATE_PATH", str(BASE_DIR / "sync_state.json")))
