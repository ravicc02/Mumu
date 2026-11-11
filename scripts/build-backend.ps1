$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$dist = Join-Path $backend "dist"
$bootstrap = Join-Path $PSScriptRoot "pyinstaller_bootstrap"
$oldPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($oldPythonPath) {
    "$bootstrap;$oldPythonPath"
} else {
    $bootstrap
}

Push-Location $backend
try {
    python ..\scripts\run_pyinstaller.py `
        --noconfirm `
        --clean `
        --onedir `
        --name "answer-studio-backend" `
        --collect-all browser_use `
        --collect-all playwright `
        --exclude-module torch `
        --exclude-module torchvision `
        --exclude-module tensorflow `
        --exclude-module onnxruntime `
        --exclude-module pandas `
        --exclude-module scipy `
        --exclude-module matplotlib `
        --exclude-module numba `
        --exclude-module llvmlite `
        --exclude-module sklearn `
        --exclude-module openpyxl `
        --exclude-module sqlalchemy `
        --runtime-hook (Join-Path $PSScriptRoot "pyinstaller_runtime_hook.py") `
        --collect-submodules app `
        --hidden-import "app.main" `
        --hidden-import "uvicorn.logging" `
        --hidden-import "uvicorn.loops.auto" `
        --hidden-import "uvicorn.protocols.http.auto" `
        --hidden-import "uvicorn.protocols.websockets.auto" `
        --hidden-import "uvicorn.lifespan.on" `
        run_server.py
} finally {
    Pop-Location
    $env:PYTHONPATH = $oldPythonPath
}

Write-Host "Backend bundle: $dist\answer-studio-backend"
