# ============================================================
# Script de re-autenticacion para NotebookLM
# Ejecutar con clic derecho -> "Ejecutar con PowerShell"
# ============================================================

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Re-autenticacion NotebookLM" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Verificar Python
Write-Host "[1/4] Verificando Python..." -ForegroundColor Yellow
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Host "ERROR: No se encontro Python. Instala Python 3.11+ desde https://python.org" -ForegroundColor Red
    Read-Host "Presiona ENTER para salir"
    exit 1
}
Write-Host "      Python encontrado: $($python.Source)" -ForegroundColor Green

# 2. Instalar notebooklm-py
Write-Host ""
Write-Host "[2/4] Instalando notebooklm-py (puede tardar 1-2 minutos)..." -ForegroundColor Yellow
& $python.Source -m pip install --upgrade notebooklm-py 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: No se pudo instalar notebooklm-py. Intenta manualmente:" -ForegroundColor Red
    Write-Host "  pip install notebooklm-py" -ForegroundColor Red
    Read-Host "Presiona ENTER para salir"
    exit 1
}
Write-Host "      Instalado correctamente" -ForegroundColor Green

# 3. Ejecutar login
Write-Host ""
Write-Host "[3/4] Abriendo navegador para login de Google..." -ForegroundColor Yellow
Write-Host "      IMPORTANTE: Inicia sesion con tu cuenta de Google" -ForegroundColor Magenta
Write-Host "      y cuando termines, VOLVE A ESTA VENTANA." -ForegroundColor Magenta
Write-Host ""
Write-Host "      Presiona ENTER para continuar..." -ForegroundColor Cyan
Read-Host

& $python.Source -m notebooklm login

# 4. Copiar archivo al escritorio
Write-Host ""
Write-Host "[4/4] Copiando archivo de credenciales al escritorio..." -ForegroundColor Yellow

$source = "$env:USERPROFILE\.notebooklm\profiles\default\storage_state.json"
$desktop = [Environment]::GetFolderPath("Desktop")
$dest = "$desktop\notebooklm_auth.json"

if (Test-Path $source) {
    Copy-Item -Path $source -Destination $dest -Force
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  EXITO!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "El archivo fue guardado en tu escritorio:" -ForegroundColor White
    Write-Host "  $dest" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Ahora subilo a Railway:" -ForegroundColor White
    Write-Host "  1. Ve al dashboard de Railway" -ForegroundColor Gray
    Write-Host "  2. Variables -> NOTEBOOKLM_AUTH_JSON" -ForegroundColor Gray
    Write-Host "  3. Pega el contenido del archivo" -ForegroundColor Gray
    Write-Host "  4. Reinicia el servicio" -ForegroundColor Gray
    Write-Host ""
} else {
    Write-Host "ERROR: No se encontro el archivo de credenciales." -ForegroundColor Red
    Write-Host "       El login puede haber fallado. Intenta de nuevo." -ForegroundColor Red
}

Read-Host "Presiona ENTER para cerrar"
