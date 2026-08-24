# Finly Startup Script
# This script starts both the backend and frontend services

Write-Host "🚀 Starting Finly Application..." -ForegroundColor Cyan
Write-Host ""

# Check if Python is installed
if (!(Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Python is not installed or not in PATH" -ForegroundColor Red
    exit 1
}

# Check if Node.js is installed
if (!(Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Node.js is not installed or not in PATH" -ForegroundColor Red
    exit 1
}

# Check if conda environment exists
$useCondaEnv = $false
$useVenv = $false

if (Get-Command conda -ErrorAction SilentlyContinue) {
    $envExists = conda env list | Select-String -Pattern "finly"
    if ($envExists) {
        Write-Host "✅ Using conda environment 'finly'" -ForegroundColor Green
        $useCondaEnv = $true
    }
}

# Check if venv exists
if (!$useCondaEnv -and (Test-Path "backend/venv")) {
    Write-Host "✅ Using Python virtual environment" -ForegroundColor Green
    $useVenv = $true
}

function Get-PortOwnerPid($port) {
    $line = netstat -ano | Select-String -Pattern "LISTENING" | Select-String -Pattern ":$port\s" | Select-Object -First 1
    if (-not $line) { return $null }
    $parts = ($line -replace '\s+', ' ').ToString().Trim().Split(' ')
    return [int]$parts[-1]
}

function Get-ProcessLabel($processId) {
    try {
        $proc = Get-Process -Id $processId -ErrorAction Stop
        return "$($proc.ProcessName) (PID $processId)"
    } catch {
        return "PID $processId"
    }
}

function Test-BackendHealth {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-BackendHealth {
    param(
        [int]$TimeoutSeconds = 90,
        [int]$IntervalSeconds = 2
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-BackendHealth) { return $true }
        Start-Sleep -Seconds $IntervalSeconds
    }
    return $false
}

$backendPort = 8000
$frontendPort = 5173
$backendPid = Get-PortOwnerPid $backendPort
$frontendPid = Get-PortOwnerPid $frontendPort
$skipBackend = $false
$skipFrontend = $false

if ($backendPid) {
    $ownerLabel = Get-ProcessLabel $backendPid
    $backendHealthy = Test-BackendHealth

    if ($backendHealthy) {
        Write-Host "✅ Backend ya responde en puerto $backendPort ($ownerLabel)" -ForegroundColor Green
        $skipBackend = $true
    } else {
        Write-Host "❌ Puerto $backendPort ocupado por $ownerLabel pero NO responde" -ForegroundColor Red
        Write-Host "   Suele ser Docker 'finly-backend' colgado. Libera el puerto:" -ForegroundColor Yellow
        Write-Host "   docker stop finly-backend" -ForegroundColor White
        Write-Host "   (o: docker compose stop backend)" -ForegroundColor White
        Write-Host ""
        Write-Host "   Luego vuelve a ejecutar .\scripts\start.ps1" -ForegroundColor Yellow
        exit 1
    }
}

if ($frontendPid) {
    Write-Host "⚠️  El puerto $frontendPort ya está en uso por $(Get-ProcessLabel $frontendPid)" -ForegroundColor Yellow
    Write-Host "   No se iniciará otro frontend." -ForegroundColor Yellow
    Write-Host ""
    $skipFrontend = $true
}

# Start Backend
if (!$skipBackend) {
    Write-Host "📦 Starting Backend (FastAPI)..." -ForegroundColor Green

    if ($useCondaEnv) {
        $condaEnvPython = "C:\Users\sergiosal\miniforge3\envs\finly\python.exe"
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd backend; Write-Host '🔧 Backend Server Starting (conda: finly)...' -ForegroundColor Yellow; & '$condaEnvPython' main.py"
    } elseif ($useVenv) {
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd backend; Write-Host '🔧 Backend Server Starting (venv)...' -ForegroundColor Yellow; .\venv\Scripts\Activate.ps1; python main.py"
    } else {
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd backend; Write-Host '🔧 Backend Server Starting...' -ForegroundColor Yellow; python main.py"
    }

    Write-Host "   Esperando backend (migraciones + arranque, hasta 90s)..." -ForegroundColor Gray
    if (-not (Wait-BackendHealth -TimeoutSeconds 90)) {
        Write-Host "❌ El backend no respondió en http://127.0.0.1:8000/api/health" -ForegroundColor Red
        Write-Host "   Revisa la ventana del backend por errores." -ForegroundColor Yellow
    } else {
        Write-Host "✅ Backend listo en http://127.0.0.1:8000" -ForegroundColor Green
    }
}

# Start Frontend
if (!$skipFrontend) {
    Write-Host "🎨 Starting Frontend (React + Vite)..." -ForegroundColor Green
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd frontend; Write-Host '⚛️  Frontend Server Starting...' -ForegroundColor Yellow; npm run dev"
} else {
    Write-Host "🎨 Frontend: reutilizando instancia existente en puerto $frontendPort" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "✅ Finly listo para usar" -ForegroundColor Green

if ($useCondaEnv) {
    Write-Host "   (Backend: conda finly)" -ForegroundColor Cyan
} elseif ($useVenv) {
    Write-Host "   (Backend: Python venv)" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "📍 Backend API: http://127.0.0.1:8000" -ForegroundColor Cyan
Write-Host "📍 Frontend App: http://127.0.0.1:5173" -ForegroundColor Cyan
Write-Host ""
Write-Host "🌐 Abre http://127.0.0.1:5173 en Chrome o Edge (NO en el navegador de Cursor)." -ForegroundColor Yellow
Write-Host "   El navegador de Cursor muestra avisos CSP que no afectan a Chrome." -ForegroundColor Yellow
Write-Host ""
Write-Host "🔑 Default Login Credentials:" -ForegroundColor Yellow
Write-Host "   Admin:  admin / admin123" -ForegroundColor White
Write-Host "   Writer: writer / writer123" -ForegroundColor White
Write-Host "   Reader: reader / reader123" -ForegroundColor White
Write-Host ""
Write-Host "Press any key to exit this window (services will continue running)..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
