$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$dist = Join-Path $backend "dist"

Push-Location $backend
try {
    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --name "answer-studio-backend" `
        --collect-all browser_use `
        --collect-all playwright `
        --hidden-import "uvicorn.logging" `
        --hidden-import "uvicorn.loops.auto" `
        --hidden-import "uvicorn.protocols.http.auto" `
        --hidden-import "uvicorn.protocols.websockets.auto" `
        --hidden-import "uvicorn.lifespan.on" `
        run_server.py
} finally {
    Pop-Location
}

Write-Host "Backend bundle: $dist\answer-studio-backend"
