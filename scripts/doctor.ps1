param(
    [ValidateRange(1, 65535)] [int] $CorePort = 5030,
    [ValidateRange(1, 65535)] [int] $PlusPort = 5031
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'stack-common.ps1')
$repoRoot = Split-Path -Parent $PSScriptRoot
$paths = Get-ChatlogStackPaths

$checks = [ordered]@{
    core_binary = Test-Path -LiteralPath $paths.Core
    python_env = Test-Path -LiteralPath (Join-Path $repoRoot '.venv\Scripts\python.exe')
    metadata = Test-Path -LiteralPath $paths.Metadata
    data_key_dpapi = Test-Path -LiteralPath $paths.DataSecret
    image_key_dpapi = Test-Path -LiteralPath $paths.ImgSecret
    core_http = Test-LocalHttp "http://127.0.0.1:$CorePort/api/v1/session"
    plus_http = Test-LocalHttp "http://127.0.0.1:$PlusPort/health"
}

if ($checks.data_key_dpapi) {
    try { $value = Unprotect-ChatlogSecret -Source $paths.DataSecret; $checks.data_key_dpapi = ($value.Length -eq 64) } catch { $checks.data_key_dpapi = $false } finally { $value = $null }
}
if ($checks.image_key_dpapi) {
    try { $value = Unprotect-ChatlogSecret -Source $paths.ImgSecret; $checks.image_key_dpapi = ($value.Length -ge 16) } catch { $checks.image_key_dpapi = $false } finally { $value = $null }
}

$checks.GetEnumerator() | ForEach-Object {
    [pscustomobject]@{ Check = $_.Key; Status = if ($_.Value) { 'OK' } else { 'MISSING/STOPPED' } }
} | Format-Table -AutoSize

if ($checks.Values -contains $false) { exit 1 }
