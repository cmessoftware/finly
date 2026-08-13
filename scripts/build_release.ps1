# Quick build script with automatic commit hash
# Usage: .\build.ps1

Write-Host "🔨 Building SmartFI with automatic version..." -ForegroundColor Cyan

# Get current commit hash
$commitHash = git log -1 --format=%h
$env:COMMIT_HASH = $commitHash

Write-Host "📌 Version: v1.2.0.$commitHash" -ForegroundColor Green
Write-Host ""

# Build and start
docker-compose build --no-cache
docker-compose up -d

Write-Host ""
Write-Host "✅ Build complete!" -ForegroundColor Green
Write-Host "🌐 Access: http://localhost:3000" -ForegroundColor Cyan
Write-Host "📋 Logs: docker-compose logs -f" -ForegroundColor Yellow
