"""
Taxpy Writer — Punto de entrada.
Arranca el bot de Telegram y el servidor web simultáneamente.
"""

import threading

from telegram_mvp_bot import WriterTelegramBot
import config
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

    # Iniciar servidor web en background
    web_thread = threading.Thread(
        target=run_web_server,
        kwargs={"host": "0.0.0.0", "port": config.API_SERVER_PORT},
        daemon=True,
    )
    web_thread.start()

    # Iniciar bot de Telegram (bloqueante)
    bot = WriterTelegramBot(config.TELEGRAM_BOT_TOKEN)
    bot.run()
