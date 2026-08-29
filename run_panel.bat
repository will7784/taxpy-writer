@echo off
rem Panel ImpuestIA (FastAPI + Jinja2). Doble click -> ventana visible -> http://localhost:8000
rem Cierra esta ventana (o Ctrl+C) para detener el panel.
title ImpuestIA Panel - http://localhost:8000
cd /d "%~dp0"
echo.
echo  ====================================
echo    ImpuestIA Panel
echo    http://localhost:8000
echo    Login: usuario y password del .env
echo  ====================================
echo.
echo  Cierra esta ventana para detener el panel.
echo.
set "PY=C:\Program Files\Python313\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" run_panel.py
echo.
echo  El panel se detuvo.
pause
