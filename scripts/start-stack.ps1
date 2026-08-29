$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'stack-common.ps1')

$repoRoot = Split-Path -Parent $PSScriptRoot
$paths = Initialize-ChatlogStackDirectories
$metadata = Get-ChatlogMetadata
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$plusScript = Join-Path $repoRoot 'chatlog_plus.py'

foreach ($required in @($paths.Core, $paths.DataSecret, $paths.ImgSecret, $python, $plusScript)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required file is missing: $required" }
}

$coreRunning = Test-LocalHttp 'http://127.0.0.1:5030/api/v1/session'
if (-not $coreRunning) {
    $dataKey = Unprotect-ChatlogSecret -Source $paths.DataSecret
    $imageKey = Unprotect-ChatlogSecret -Source $paths.ImgSecret
    $savedEnv = @{}
    foreach ($name in @('CHATLOG_DIR', 'CHATLOG_HTTP_ADDR', 'CHATLOG_PLATFORM', 'CHATLOG_VERSION', 'CHATLOG_FULL_VERSION', 'CHATLOG_DATA_DIR', 'CHATLOG_WORK_DIR', 'CHATLOG_DATA_KEY', 'CHATLOG_IMG_KEY', 'CHATLOG_AUTO_DECRYPT')) {
        $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    try {
        $env:CHATLOG_DIR = $paths.CoreState
        $env:CHATLOG_HTTP_ADDR = '127.0.0.1:5030'
        $env:CHATLOG_PLATFORM = [string]$metadata.platform
        $env:CHATLOG_VERSION = [string]$metadata.version
        $env:CHATLOG_FULL_VERSION = [string]$metadata.full_version
        $env:CHATLOG_DATA_DIR = [string]$metadata.data_dir
        $env:CHATLOG_WORK_DIR = $paths.Work
        $env:CHATLOG_DATA_KEY = $dataKey
        $env:CHATLOG_IMG_KEY = $imageKey
        $env:CHATLOG_AUTO_DECRYPT = 'true'

        Start-Process -FilePath $paths.Core `
            -ArgumentList @('server') `
            -WorkingDirectory $paths.Home `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $paths.Logs 'core-stdout.log') `
            -RedirectStandardError (Join-Path $paths.Logs 'core-stderr.log')
    } finally {
        foreach ($entry in $savedEnv.GetEnumerator()) {
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
        }
        $dataKey = $null
        $imageKey = $null
        [GC]::Collect()
    }
}

$deadline = [DateTime]::UtcNow.AddSeconds(30)
while ([DateTime]::UtcNow -lt $deadline -and -not (Test-LocalHttp 'http://127.0.0.1:5030/api/v1/session')) {
    Start-Sleep -Milliseconds 500
}

if (-not (Test-LocalHttp 'http://127.0.0.1:5030/api/v1/session')) {
    throw "Chatlog core did not become ready. Check $($paths.Logs) without sharing secret files."
}

$env:CHATLOG_PLUS_STATE_DIR = Join-Path $paths.Home 'plus-state'
& (Join-Path $PSScriptRoot 'start-chatlog-plus.ps1')

Write-Host 'WeChat Chatlog Stack is ready: core http://127.0.0.1:5030, enhanced API http://127.0.0.1:5031'
