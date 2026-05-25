"""
Configuración centralizada — Escritor Taxpy (NotebookLM + GPT-4o)
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
# OpenAI (escritura)
# ============================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# ============================================
# Google Gemini (STT/TTS)
# ============================================
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# ============================================
# NotebookLM
# ============================================
NOTEBOOKLM_NOTEBOOK_NAME = os.getenv("NOTEBOOKLM_NOTEBOOK_NAME", "Taxpy Conocimiento")
NOTEBOOKLM_NOTEBOOK_SECONDARY = os.getenv("NOTEBOOKLM_NOTEBOOK_SECONDARY", "")

# Para Railway / headless: pega el contenido de storage_state.json aquí
# Si la variable es muy larga para Railway, sube un archivo notebooklm_auth.json a la raíz
_auth_file = BASE_DIR / "notebooklm_auth.json"
if _auth_file.exists():
    # Fallback archivo: preferido porque no sufre problemas de escape/saltos de linea
    NOTEBOOKLM_AUTH_JSON = _auth_file.read_text(encoding="utf-8").strip()
else:
    NOTEBOOKLM_AUTH_JSON = os.getenv("NOTEBOOKLM_AUTH_JSON", "").strip()

# ============================================
# Writer
# ============================================
WRITER_MAX_TOKENS = int(os.getenv("WRITER_MAX_TOKENS", "8000"))
WRITER_TEMPERATURE = float(os.getenv("WRITER_TEMPERATURE", "0.5"))

# ============================================
# Paths
# ============================================
TELEGRAM_DB_PATH = Path(os.getenv("TELEGRAM_DB_PATH", str(BASE_DIR / "taxpy_writer.sqlite3")))
EXPORTS_DIR = Path(os.getenv("EXPORTS_DIR", str(BASE_DIR / "exports")))
EXPORTS_DIR.mkdir(exist_ok=True)

# ============================================
# Agente de escritura
# ============================================
AGENT_MD_FILE = BASE_DIR / "agent.md"

# ============================================
# Railway / Server (healthcheck + frontend)
# ============================================
API_SERVER_PORT = int(os.getenv("PORT", os.getenv("API_SERVER_PORT", "8000")))

# ============================================
# Admin panel credentials (fallback seguro)
# ============================================
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "will")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "anwi7784")
SESSION_SECRET = os.getenv("SESSION_SECRET", "taxpy-writer-secret-change-me")
