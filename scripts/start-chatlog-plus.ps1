$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$script = Join-Path $repoRoot 'chatlog_plus.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Virtual environment not found. Run scripts\setup.ps1 first.'
}

if (-not $env:CHATLOG_PLUS_STATE_DIR) {
    $env:CHATLOG_PLUS_STATE_DIR = Join-Path $env:LOCALAPPDATA 'ChatlogPlus'
}

$logDir = Join-Path $env:CHATLOG_PLUS_STATE_DIR 'process-logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$alreadyRunning = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*$script*serve*"
}
if ($alreadyRunning) {
    Write-Host 'Chatlog Plus is already running.'
    exit 0
}

Start-Process -FilePath $python `
    -ArgumentList @($script, 'serve') `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'stdout.log') `
    -RedirectStandardError (Join-Path $logDir 'stderr.log')

$plusPort = if ($env:CHATLOG_PLUS_PORT) { $env:CHATLOG_PLUS_PORT } else { '5031' }
Write-Host "Chatlog Plus started at http://127.0.0.1:$plusPort"
