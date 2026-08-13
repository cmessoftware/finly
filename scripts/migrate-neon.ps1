# Apply Alembic migrations to Neon (Render PROD database)
# Usage:
#   $env:NEON_DATABASE_URL = "postgresql://..."   # from Render smartfi-api Environment
#   .\scripts\migrate-neon.ps1
# Or set NEON_DATABASE_URL in project .env

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\postgres-helpers.ps1"

$neonUrl = Get-NeonDatabaseUrl
if (-not $neonUrl) {
    Write-Host "NEON_DATABASE_URL not found. Set it in .env or pass -DatabaseUrl" -ForegroundColor Red
    exit 1
}

Write-Host "Applying Alembic migrations to Neon..." -ForegroundColor Cyan
Write-Host "  URL: $(Format-RedactedDbUrl $neonUrl)" -ForegroundColor Gray

$backendDir = Join-Path (Get-ProjectRoot) "backend"
$python = $null

if (Get-Command conda -ErrorAction SilentlyContinue) {
    $condaPython = "C:\Users\sergiosal\miniforge3\envs\finly\python.exe"
    if (Test-Path $condaPython) { $python = $condaPython }
}

if (-not $python) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
}

if (-not $python) {
    Write-Host "Python not found." -ForegroundColor Red
    exit 1
}

$env:DATABASE_URL = $neonUrl
Push-Location $backendDir
try {
    & $python -m alembic current
    & $python -m alembic upgrade head
    Write-Host ""
    & $python -m alembic current
    Write-Host ""
    Write-Host "Done. Expected head: d4e5f6a7b8c9" -ForegroundColor Green
} finally {
    Pop-Location
}
