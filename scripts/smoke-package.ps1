param(
    [string]$ReleaseDirectory = "desktop/release",
    [int]$Port = 18877
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$version = (Get-Content -LiteralPath (Join-Path $root "VERSION") -Raw).Trim()
$releaseRoot = if ([IO.Path]::IsPathRooted($ReleaseDirectory)) { $ReleaseDirectory } else { Join-Path $root $ReleaseDirectory }
$productName = (Get-Content -LiteralPath (Join-Path $root "desktop/package.json") -Encoding UTF8 -Raw | ConvertFrom-Json).build.productName
$desktopExe = Join-Path $releaseRoot ("win-unpacked/" + $productName + ".exe")
$backendExe = Join-Path $releaseRoot "win-unpacked/resources/backend/answer-studio-backend.exe"
if (!(Test-Path -LiteralPath $desktopExe -PathType Leaf) -or !(Test-Path -LiteralPath $backendExe -PathType Leaf)) { throw "Packaged executables are missing" }
$productVersion = (Get-Item -LiteralPath $desktopExe).VersionInfo.ProductVersion
if ($productVersion -ne $version) { throw "Desktop executable version differs: $productVersion / $version" }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw "Smoke-test port $Port is already occupied" }
$smokeRoot = Join-Path ([IO.Path]::GetTempPath()) ("assignment-studio-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $smokeRoot | Out-Null
$previousDataRoot = $env:ANSWER_STUDIO_DATA
$process = $null
try {
    $env:ANSWER_STUDIO_DATA = $smokeRoot
    $process = Start-Process -FilePath $backendExe -ArgumentList @("--host", "127.0.0.1", "--port", "$Port") -WorkingDirectory (Split-Path -Parent $backendExe) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $smokeRoot "stdout.log") -RedirectStandardError (Join-Path $smokeRoot "stderr.log")
    $deadline = (Get-Date).AddSeconds(60)
    $health = $null
    while ((Get-Date) -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) { throw "Packaged backend exited before becoming healthy; inspect $smokeRoot" }
        try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2 } catch { Start-Sleep -Milliseconds 500; continue }
        if ($health.ok -and $health.version -eq $version) { break }
        throw "Packaged backend health or version differs"
    }
    if (!$health -or !$health.ok -or $health.version -ne $version) { throw "Packaged backend did not become healthy; inspect $smokeRoot" }
    [ordered]@{ version = $version; desktop_product_version = $productVersion; packaged_backend_healthy = $true; isolated_data = $true } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseRoot "smoke-result.json") -Encoding utf8
    Write-Host "Packaged backend and desktop metadata verified: $version"
} finally {
    if ($process) { $process.Refresh(); if (!$process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue } }
    $env:ANSWER_STUDIO_DATA = $previousDataRoot
}
