[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [switch] $KeepKeys
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'stack-common.ps1')
$paths = Get-ChatlogStackPaths

& (Join-Path $PSScriptRoot 'stop-stack.ps1')
& (Join-Path $PSScriptRoot 'unregister-startup.ps1') -Confirm:$false

if ($KeepKeys) {
    foreach ($path in @($paths.Bin, $paths.CoreState, $paths.Work, $paths.Logs, (Join-Path $paths.Home 'downloads'), (Join-Path $paths.Home 'plus-state'), (Join-Path $paths.Home 'source'))) {
        if ((Test-Path -LiteralPath $path) -and $PSCmdlet.ShouldProcess($path, 'Remove managed stack data')) {
            $resolved = (Resolve-Path -LiteralPath $path).Path
            if (-not $resolved.StartsWith($paths.Home, [StringComparison]::OrdinalIgnoreCase)) { throw "Refusing to remove path outside stack home: $resolved" }
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
    }
    Write-Host 'Stack removed. DPAPI keys and WeChat metadata were kept.'
} elseif ((Test-Path -LiteralPath $paths.Home) -and $PSCmdlet.ShouldProcess($paths.Home, 'Remove the stack including DPAPI key blobs and transcript cache')) {
    $resolvedHome = (Resolve-Path -LiteralPath $paths.Home).Path
    $expectedHome = [IO.Path]::GetFullPath($paths.Home).TrimEnd('\')
    if ($resolvedHome.TrimEnd('\') -ne $expectedHome) { throw 'Resolved stack home did not match the expected removal target.' }
    Remove-Item -LiteralPath $resolvedHome -Recurse -Force
    Write-Host 'Stack data and its user-scoped DPAPI key blobs were removed permanently.'
}
