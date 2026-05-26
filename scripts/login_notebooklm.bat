@echo off
title Re-autenticacion NotebookLM
powershell -ExecutionPolicy Bypass -File "%~dp0login_notebooklm.ps1"
pause
