param([switch]$SkipCoreDownload)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$requirementsPath = Join-Path $PSScriptRoot 'requirements.txt'
$stampPath = Join-Path $PSScriptRoot '.venv\requirements.sha256'
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not (Test-Path -LiteralPath $pythonPath)) {
    if ($uvCommand) {
        & uv venv --python 3.12 (Join-Path $PSScriptRoot '.venv')
    } else {
        & python -m venv (Join-Path $PSScriptRoot '.venv')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create Python environment. Install Python 3.11+ or uv.' }
}
$requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
$installedHash = if (Test-Path -LiteralPath $stampPath) { (Get-Content -LiteralPath $stampPath -Raw).Trim() } else { '' }
if ($requirementsHash -ne $installedHash) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv\Lib\site-packages\pip\__init__.py'))) {
        & $pythonPath -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { throw 'Unable to prepare pip.' }
    }
    & $pythonPath -m pip install --disable-pip-version-check -r $requirementsPath
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and retry.' }
    [IO.File]::WriteAllText($stampPath, $requirementsHash, (New-Object Text.UTF8Encoding($false)))
}
$corePath = Join-Path $PSScriptRoot 'bin\mihomo.exe'
if (-not $SkipCoreDownload -and -not $env:MIHOMO_BIN -and -not (Test-Path -LiteralPath $corePath)) {
    & $pythonPath (Join-Path $PSScriptRoot 'scripts\install_core.py')
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Core download failed. The page can open, but proxy service needs the core installed.' }
}
$webPort = if ($env:PROXY_MANAGER_PORT) { $env:PROXY_MANAGER_PORT } else { '8787' }
Write-Host "Open http://127.0.0.1:$webPort - Press Ctrl+C to stop."
& $pythonPath (Join-Path $PSScriptRoot 'run.py')
exit $LASTEXITCODE
