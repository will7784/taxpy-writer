#!/bin/bash
set -e

echo "=== Taxpy Writer Bot startup ==="

# notebooklm-py espera el archivo en ~/.notebooklm/profiles/default/storage_state.json
mkdir -p /root/.notebooklm/profiles/default

STORAGE_FILE="/root/.notebooklm/profiles/default/storage_state.json"
AUTH_FILE="/app/notebooklm_auth.json"

# Prioridad 1: archivo local notebooklm_auth.json (actualizado via dashboard web)
if [ -f "$AUTH_FILE" ]; then
    cp "$AUTH_FILE" "$STORAGE_FILE"
    echo "✅ Credenciales NotebookLM sincronizadas desde archivo local (dashboard)"
# Prioridad 2: variable de entorno NOTEBOOKLM_AUTH_JSON (fallback para deploys limpios)
elif [ -n "$NOTEBOOKLM_AUTH_JSON" ]; then
    echo "$NOTEBOOKLM_AUTH_JSON" > "$STORAGE_FILE"
    echo "✅ Credenciales NotebookLM sincronizadas desde variable de entorno"
else
    echo "⚠️ No hay credenciales de NotebookLM configuradas."
    echo "   Sube un archivo desde el dashboard o agrega NOTEBOOKLM_AUTH_JSON en Railway."
fi

exec python main.py
