"""Arranca solo el panel web (sin el bot de Telegram).

Útil para trabajar local sin entrar en conflicto con la instancia del bot
que corre en producción (mismo TELEGRAM_BOT_TOKEN).

Uso:
    python run_panel.py [--port 8000]
"""
import argparse

from web_server import run_web_server

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run_web_server(host=args.host, port=args.port)
