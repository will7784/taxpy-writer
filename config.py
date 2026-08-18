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

# Google Gemini (recomendado: 1M contexto, leyes completas)
# =================================================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Kimi / Moonshot (1M contexto, leyes completas — recomendado para modo estudio)
# API OpenAI-compatible: https://platform.moonshot.ai
# =================================================================
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1")
KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-k2-0905-preview")
KIMI_MAX_CONTEXT = int(os.getenv("KIMI_MAX_CONTEXT", "1000000"))

# DeepSeek (128K contexto, ~$0.14/M input tokens, OpenAI-compatible)
# =================================================================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# Cualquier API OpenAI-compatible (Qwen, Moonshot, Zhipu, etc.)
# =================================================================
CUSTOM_LLM_API_KEY = os.getenv("CUSTOM_LLM_API_KEY", "")
CUSTOM_LLM_BASE_URL = os.getenv("CUSTOM_LLM_BASE_URL", "")
CUSTOM_LLM_MODEL = os.getenv("CUSTOM_LLM_MODEL", "")
CUSTOM_LLM_MAX_CONTEXT = int(os.getenv("CUSTOM_LLM_MAX_CONTEXT", "128000"))

# ============================================
# Supabase (OPCIONAL — solo usage_logs si esta configurado)
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
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "will")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "anwi7784")
SESSION_SECRET = os.getenv("SESSION_SECRET", "impuestia-secret-change-me")

# ============================================
# Sync vault producción → Obsidian local
# ============================================
# URL del panel de producción desde donde sync_vault.py descarga el vault.
SYNC_PROD_URL = os.getenv("SYNC_PROD_URL", "https://taxpy-writer-production.up.railway.app")
SYNC_STATE_PATH = Path(os.getenv("SYNC_STATE_PATH", str(BASE_DIR / "sync_state.json")))
