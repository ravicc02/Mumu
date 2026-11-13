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
    node ..\scripts\version.mjs check
    if ($LASTEXITCODE -ne 0) { throw "Application versions are not aligned" }
    python ..\scripts\write_build_info.py
    if ($LASTEXITCODE -ne 0) { throw "Diagnostic build metadata generation failed" }
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
        --add-data "../VERSION;app" `
        --add-data "app/services/learning/runtime.js;app/services/learning" `
        --add-data "app/services/learning/ocs-study.js;app/services/learning" `
        --add-data "app/services/learning/platform-runtime.js;app/services/learning" `
        --add-data "app/services/platforms/catalog.json;app/services/platforms" `
        --add-data "app/services/attendance/share.html;app/services/attendance" `
        --add-data "app/services/attendance/jsQR.js;app/services/attendance" `
        --add-data "app/services/attendance/JSQR-LICENSE;app/services/attendance" `
        --add-data "app/services/learning/OCS-LICENSE;app/services/learning" `
        --add-data "build/diagnostic-build.json;app/diagnostics" `
        --hidden-import "app.main" `
        --hidden-import "uvicorn.logging" `
        --hidden-import "uvicorn.loops.auto" `
        --hidden-import "uvicorn.protocols.http.auto" `
        --hidden-import "uvicorn.protocols.websockets.auto" `
        --hidden-import "uvicorn.lifespan.on" `
        run_server.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller backend build failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
    $env:PYTHONPATH = $oldPythonPath
}

Write-Host "Backend bundle: $dist\answer-studio-backend"
