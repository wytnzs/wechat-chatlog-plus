[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'stack-common.ps1')

$startScript = Join-Path $PSScriptRoot 'start-stack.ps1'
$stackHome = Get-ChatlogStackHome
$startupDir = [Environment]::GetFolderPath('Startup')
$launcher = Join-Path $startupDir 'WeChatChatlogStack.cmd'
$content = "@echo off`r`nset `"WECHAT_CHATLOG_HOME=$stackHome`"`r`nstart `"`" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`"`r`n"

if ($PSCmdlet.ShouldProcess($launcher, 'Register user logon startup')) {
    [IO.File]::WriteAllText($launcher, $content, [Text.ASCIIEncoding]::new())
    Write-Host "Startup registered: $launcher"
}
