$ErrorActionPreference = 'Stop'

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

Write-Host 'Chatlog Plus is ready. Run scripts\start-chatlog-plus.ps1 after starting Chatlog core.'
