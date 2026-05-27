"""
Taxpy RAG — Punto de entrada.
Arranca el servidor web en el main thread y el bot de Telegram en background.
"""

import threading

import config
from telegram_mvp_bot import WriterTelegramBot
from web_server import run_web_server

if __name__ == "__main__":
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN no configurado. "
            "Revisa tu archivo .env o variables de entorno."
        )
    if not config.OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY no configurado. "
            "Se requiere para redactar contenido con GPT-4o."
        )
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
        raise RuntimeError(
            "SUPABASE_URL y SUPABASE_SERVICE_KEY no configurados. "
            "Se requieren para el RAG vector database. "
            "Revisa tu archivo .env o variables de entorno."
        )

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

    # Web server en main thread (uvicorn maneja señales correctamente aquí)
    run_web_server(host="0.0.0.0", port=config.API_SERVER_PORT)
