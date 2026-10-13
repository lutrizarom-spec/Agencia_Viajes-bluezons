# Ejecuta la API y el frontend sin Docker.
# La API sirve la UI en http://127.0.0.1:8000/app/ (mismo origen, sin nginx ni CORS).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "Creando entorno virtual e instalando dependencias..."
    python -m venv .venv
    & $python -m pip install --upgrade pip
    & $python -m pip install -r requirements.txt
}

if (-not $env:AGENCIA_JWT_SECRET) {
    $env:AGENCIA_JWT_SECRET = (& $python -c "import secrets; print(secrets.token_urlsafe(48))")
    Write-Host "AGENCIA_JWT_SECRET generado para esta sesión."
}
if (-not $env:AGENCIA_DB_PATH) {
    $env:AGENCIA_DB_PATH = Join-Path $root "agencia.db"
}

Write-Host "Frontend: http://127.0.0.1:8000/app/"
Write-Host "Docs:     http://127.0.0.1:8000/docs"
Write-Host "Detener con Ctrl+C."
& $python -m uvicorn main_api:create_app --factory --host 127.0.0.1 --port 8000
