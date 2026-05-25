#!/bin/bash
set -e

echo "=== Taxpy Writer Bot startup ==="

# notebooklm-py espera el archivo en ~/.notebooklm/profiles/default/storage_state.json
# Nosotros lo mantenemos en /app/notebooklm_auth.json para facilidad de deploy
mkdir -p /root/.notebooklm/profiles/default
if [ -f /app/notebooklm_auth.json ]; then
    cp /app/notebooklm_auth.json /root/.notebooklm/profiles/default/storage_state.json
    echo "✅ Credenciales NotebookLM sincronizadas"
else
    echo "⚠️ No existe /app/notebooklm_auth.json — NotebookLM no funcionará hasta subir credenciales"
fi

exec python main.py
