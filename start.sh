#!/bin/bash
set -e

echo "=== Taxpy Writer Bot startup ==="

# notebooklm-py espera el archivo en ~/.notebooklm/profiles/default/storage_state.json
mkdir -p /root/.notebooklm/profiles/default

STORAGE_FILE="/root/.notebooklm/profiles/default/storage_state.json"

# Prioridad 1: variable de entorno NOTEBOOKLM_AUTH_JSON (permite actualizar desde Railway sin commit)
if [ -n "$NOTEBOOKLM_AUTH_JSON" ]; then
    echo "$NOTEBOOKLM_AUTH_JSON" > "$STORAGE_FILE"
    echo "✅ Credenciales NotebookLM sincronizadas desde variable de entorno"
# Prioridad 2: archivo local notebooklm_auth.json (fallback)
elif [ -f /app/notebooklm_auth.json ]; then
    cp /app/notebooklm_auth.json "$STORAGE_FILE"
    echo "✅ Credenciales NotebookLM sincronizadas desde archivo local"
else
    echo "⚠️ No hay credenciales de NotebookLM configuradas. Agrega NOTEBOOKLM_AUTH_JSON en Railway o sube notebooklm_auth.json"
fi

exec python main.py
