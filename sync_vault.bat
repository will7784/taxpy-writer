@echo off
rem Sync del vault de produccion hacia el Obsidian local (Dropbox).
rem Usado por la tarea programada "ImpuestiaVaultSync".
cd /d "C:\Users\lyf-a\Dropbox\AGENTES Y CODIGO CON IA\AGENTES BACKOFFICE\Agente Tributario"
"C:\Program Files\Python313\python.exe" sync_vault.py >> sync_vault.log 2>&1
