[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'stack-common.ps1')

$repoRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $repoRoot 'core-release.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$paths = Initialize-ChatlogStackDirectories
$downloadDir = Join-Path $paths.Home 'downloads'
$download = Join-Path $downloadDir 'chatlog-core-windows-amd64.exe.download'

if ((Test-Path -LiteralPath $paths.Core) -and ((Get-FileHash -LiteralPath $paths.Core -Algorithm SHA256).Hash -eq $manifest.sha256)) {
    Write-Host 'Pinned Chatlog core is already installed and verified.'
    exit 0
}

if (-not $PSCmdlet.ShouldProcess($paths.Core, "Download and install pinned Chatlog core $($manifest.tag)")) {
    exit 0
}

New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
$lastError = $null
for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $manifest.url -OutFile $download -TimeoutSec 180
        $lastError = $null
        break
    } catch {
        $lastError = $_
        if ($attempt -lt 3) { Start-Sleep -Seconds ([Math]::Pow(2, $attempt)) }
    }
}
if ($lastError) { throw "Core download failed after three attempts: $($lastError.Exception.Message)" }

$actualHash = (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne ([string]$manifest.sha256).ToLowerInvariant()) {
    Remove-Item -LiteralPath $download -Force
    throw 'Core download checksum did not match the pinned release manifest.'
}

Move-Item -LiteralPath $download -Destination $paths.Core -Force
Write-Host "Pinned Chatlog core installed and verified: $($paths.Core)"
