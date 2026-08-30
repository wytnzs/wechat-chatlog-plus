$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'stack-common.ps1')

$repoRoot = Split-Path -Parent $PSScriptRoot
$paths = Initialize-ChatlogStackDirectories
$sourceRepo = 'https://github.com/imldy/chatlog.git'
$sourceCommit = 'eb9572ee028cbaa72a50749b9dfe6630b317228f'
$patchFile = Join-Path $repoRoot 'patches\chatlog-safe-local.patch'

foreach ($command in @('git', 'go', 'gcc')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required build tool '$command' was not found. Install Git, Go 1.24+, and a Windows GCC toolchain, or use the prebuilt release installer."
    }
}

if (Test-Path -LiteralPath $paths.Source) {
    if (-not (Test-Path -LiteralPath (Join-Path $paths.Source '.git'))) {
        throw "Managed source path exists but is not a Git checkout: $($paths.Source)"
    }
    $statusLines = @(& git -C $paths.Source status --porcelain)
    if ($statusLines.Count -gt 0) {
        $currentCommit = (& git -C $paths.Source rev-parse HEAD).Trim()
        $allowedPatchFiles = @(
            'cmd/chatlog/cmd_server.go',
            'internal/chatlog/conf/conf.go',
            'internal/chatlog/manager.go',
            'internal/wechat/key/windows/v3_windows.go',
            'internal/wechat/key/windows/v4_windows.go'
        )
        $changedFiles = $statusLines | ForEach-Object { $_.Substring(3).Replace('\', '/') }
        $unexpected = @($changedFiles | Where-Object { $_ -notin $allowedPatchFiles })
        & git -C $paths.Source apply --reverse --check $patchFile 2>$null
        $onlyExpectedPatch = ($LASTEXITCODE -eq 0 -and $unexpected.Count -eq 0 -and $currentCommit -eq $sourceCommit)
        if (-not $onlyExpectedPatch) {
            throw 'Managed Chatlog source has changes other than the expected checked-in patch. Preserve or remove them explicitly before rebuilding.'
        }
    }
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $paths.Source) | Out-Null
    & git clone --filter=blob:none $sourceRepo $paths.Source
    if ($LASTEXITCODE -ne 0) { throw 'Chatlog source clone failed.' }
}

& git -C $paths.Source fetch origin $sourceCommit --depth 1
if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch the pinned Chatlog source commit.' }
& git -C $paths.Source checkout --detach $sourceCommit
if ($LASTEXITCODE -ne 0) { throw 'Unable to check out the pinned Chatlog source commit.' }

& git -C $paths.Source apply --check $patchFile 2>$null
if ($LASTEXITCODE -eq 0) {
    & git -C $paths.Source apply $patchFile
    if ($LASTEXITCODE -ne 0) { throw 'The local-safety patch could not be applied.' }
} else {
    & git -C $paths.Source apply --reverse --check $patchFile 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'The pinned source does not match the expected local-safety patch.'
    }
}

$oldCgo = $env:CGO_ENABLED
try {
    $env:CGO_ENABLED = '1'
    Push-Location $paths.Source
    try {
        & go build -trimpath -ldflags '-X github.com/sjzar/chatlog/pkg/version.Version=eb9572e-local2 -w -s' -o $paths.Core main.go
        if ($LASTEXITCODE -ne 0) { throw 'Chatlog core build failed.' }
    } finally {
        Pop-Location
    }
} finally {
    if ($null -eq $oldCgo) { Remove-Item Env:CGO_ENABLED -ErrorAction SilentlyContinue } else { $env:CGO_ENABLED = $oldCgo }
}

Write-Host "Chatlog core built: $($paths.Core)"
