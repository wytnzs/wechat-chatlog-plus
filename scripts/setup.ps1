param(
    [switch] $BuildCoreFromSource,
    [switch] $SkipCore
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvDir = Join-Path $repoRoot '.venv'
$requirements = Join-Path $repoRoot 'requirements.txt'

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m venv $venvDir
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m venv $venvDir
} else {
    throw 'Python 3 was not found. Install Python 3.10 or newer first.'
}

$python = Join-Path $venvDir 'Scripts\python.exe'
& $python -m pip install --upgrade pip
& $python -m pip install -r $requirements

if (-not $SkipCore) {
    if ($BuildCoreFromSource) {
        & (Join-Path $PSScriptRoot 'build-chatlog-core.ps1')
    } else {
        & (Join-Path $PSScriptRoot 'install-chatlog-core.ps1')
    }
}

Write-Host 'Setup complete. Keep WeChat open, then run scripts\save-wechat-keys.ps1 once.'
