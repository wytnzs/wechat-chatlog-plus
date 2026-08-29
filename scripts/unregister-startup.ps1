[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$launcher = Join-Path ([Environment]::GetFolderPath('Startup')) 'WeChatChatlogStack.cmd'
if ((Test-Path -LiteralPath $launcher) -and $PSCmdlet.ShouldProcess($launcher, 'Remove user logon startup')) {
    Remove-Item -LiteralPath $launcher -Force
    Write-Host 'Startup registration removed.'
} elseif (-not (Test-Path -LiteralPath $launcher)) {
    Write-Host 'Startup registration is not present.'
}
