"""
ImpuestIA — Asistente Tributario Chileno (local).
Arranca el servidor web en el main thread y el bot de Telegram en background.
"""

import threading

import config
from context_rag import law_loader
from telegram_mvp_bot import WriterTelegramBot
from web_server import run_web_server

if __name__ == "__main__":
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN no configurado. "
            "Revisa tu archivo .env o variables de entorno."
        )
    if not config.OPENAI_API_KEY and not config.GEMINI_API_KEY and not getattr(config, "KIMI_API_KEY", ""):
        raise RuntimeError(
            "Se requiere KIMI_API_KEY, OPENAI_API_KEY o GEMINI_API_KEY. "
            "Revisa tu archivo .env o variables de entorno."
        )

    # Precargar leyes en memoria al iniciar
    print(f"[ImpuestIA] Cargando leyes...")
    law_loader._ensure_loaded()
    print(f"[ImpuestIA] {len(law_loader.all())} leyes listas (~{law_loader.total_tokens():,} tokens)")

    def _run_bot() -> None:
        try:
            bot = WriterTelegramBot(config.TELEGRAM_BOT_TOKEN)
            bot.run()
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("Bot crashed: %s", e)

    # Bot en thread daemon (background)
    bot_thread = threading.Thread(target=_run_bot, daemon=True)
    bot_thread.start()

    # Web server en main thread
    run_web_server(host="0.0.0.0", port=config.API_SERVER_PORT)
