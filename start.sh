#!/bin/bash
set -e

echo "=== ImpuestIA Bot startup ==="
echo "Motor: context_rag (leyes completas + notas aprobadas + arboles de decision)"
echo "Front: FastAPI + Jinja2 (puerto 8000)"

exec python main.py
