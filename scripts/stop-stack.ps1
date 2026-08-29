$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'stack-common.ps1')
$paths = Get-ChatlogStackPaths
$repoRoot = Split-Path -Parent $PSScriptRoot
$plusScript = Join-Path $repoRoot 'chatlog_plus.py'

$targets = Get-CimInstance Win32_Process | Where-Object {
    ($_.ExecutablePath -eq $paths.Core) -or ($_.CommandLine -and $_.CommandLine.Contains($plusScript))
}

foreach ($target in $targets) {
    Stop-Process -Id $target.ProcessId -Force
}

Write-Host "Stopped $(@($targets).Count) stack process(es)."
