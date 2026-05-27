# ============================================================
# Re-autenticacion NotebookLM - Version mejorada
# ============================================================

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Re-autenticacion NotebookLM" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Verificar Python
Write-Host "[1/5] Verificando Python..." -ForegroundColor Yellow
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Host "ERROR: No se encontro Python. Instala Python 3.11+ desde https://python.org" -ForegroundColor Red
    Read-Host "Presiona ENTER para salir"
    exit 1
}
Write-Host "      OK: $($python.Source)" -ForegroundColor Green

# 2. Instalar notebooklm-py
Write-Host ""
Write-Host "[2/5] Instalando notebooklm-py..." -ForegroundColor Yellow
& $python.Source -m pip install --upgrade notebooklm-py | Out-Null
Write-Host "      OK" -ForegroundColor Green

# 3. Instalar navegador Playwright
Write-Host ""
Write-Host "[3/5] Instalando navegador Chromium (puede tardar 1-2 min)..." -ForegroundColor Yellow
& $python.Source -m playwright install chromium | Out-Null
Write-Host "      OK" -ForegroundColor Green

# 4. Login (abre navegador)
Write-Host ""
Write-Host "[4/5] Abriendo navegador para login de Google..." -ForegroundColor Yellow
Write-Host ""
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "  ATENCION" -ForegroundColor Magenta
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "Se va a abrir Chrome. Tenes que:" -ForegroundColor White
Write-Host "  1. Iniciar sesion con tu cuenta de Google" -ForegroundColor White
Write-Host "  2. Esperar a que cargue NotebookLM" -ForegroundColor White
Write-Host "  3. Cerrar el navegador" -ForegroundColor White
Write-Host "  4. Volver a esta ventana" -ForegroundColor White
Write-Host ""
Write-Host "Presiona ENTER para abrir el navegador..." -ForegroundColor Cyan
Read-Host

# Ejecutar login
& $python.Source -m notebooklm login

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Login completado" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# 5. Copiar archivo al escritorio
Write-Host "[5/5] Copiando archivo al escritorio..." -ForegroundColor Yellow

$source = "$env:USERPROFILE\.notebooklm\profiles\default\storage_state.json"
$desktop = [Environment]::GetFolderPath("Desktop")
$dest = "$desktop\notebooklm_auth.json"

if (Test-Path $source) {
    $size = (Get-Item $source).Length
    if ($size -lt 1000) {
        Write-Host "ADVERTENCIA: El archivo es muy pequeno ($size bytes)." -ForegroundColor Yellow
        Write-Host "El login puede no haberse completado correctamente." -ForegroundColor Yellow
    } else {
        Copy-Item -Path $source -Destination $dest -Force
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  EXITO!" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "Archivo guardado en tu escritorio:" -ForegroundColor White
        Write-Host "  $dest" -ForegroundColor Cyan
        Write-Host "  (Tamaño: $size bytes)" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Ahora subilo a Railway:" -ForegroundColor White
        Write-Host "  1. Abri el archivo con Bloc de notas" -ForegroundColor Gray
        Write-Host "  2. Selecciona todo (Ctrl+A) y copia (Ctrl+C)" -ForegroundColor Gray
        Write-Host "  3. Railway -> Variables -> NOTEBOOKLM_AUTH_JSON" -ForegroundColor Gray
        Write-Host "  4. Pega y guarda" -ForegroundColor Gray
        Write-Host "  5. Reinicia el servicio" -ForegroundColor Gray
        Write-Host ""
    }
} else {
    Write-Host "ERROR: No se encontro el archivo de credenciales." -ForegroundColor Red
    Write-Host "       El login puede haber fallado." -ForegroundColor Red
}

Read-Host "Presiona ENTER para cerrar"
