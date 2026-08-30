param(
    [Alias('Pid')] [int] $ProcessId = 0,
    [switch] $Force,
    [switch] $Quiet
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'stack-common.ps1')
$paths = Initialize-ChatlogStackDirectories

if (-not (Test-Path -LiteralPath $paths.Core)) {
    throw 'Chatlog core is missing. Run scripts\setup.ps1 first.'
}

$oldConfigDir = $env:CHATLOG_DIR
$env:CHATLOG_DIR = $paths.CoreState
try {
    function Invoke-KeyScan([int] $TargetProcessId) {
        $arguments = @('key')
        if ($TargetProcessId -gt 0) { $arguments += @('--pid', $TargetProcessId) }
        if ($Force) { $arguments += '--force' }
        $scanOutput = (& $paths.Core @arguments 2>&1 | Out-String)
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $scanOutput }
    }

    $scan = Invoke-KeyScan -TargetProcessId $ProcessId
    $output = $scan.Output
    if ($scan.ExitCode -ne 0) { throw 'Chatlog key extraction failed. Keep WeChat open and try again.' }

    if ($ProcessId -eq 0 -and -not [regex]::IsMatch($output, '(?m)^Data Key:')) {
        $candidateIds = [regex]::Matches($output, '(?m)^PID: (\d+)\.') | ForEach-Object { [int]$_.Groups[1].Value }
        foreach ($candidateId in $candidateIds) {
            $scan = Invoke-KeyScan -TargetProcessId $candidateId
            if ($scan.ExitCode -eq 0 -and [regex]::IsMatch($scan.Output, '(?m)^Data Key: \[[0-9a-fA-F]{64}\]')) {
                $output = $scan.Output
                break
            }
        }
    }
} finally {
    if ($null -eq $oldConfigDir) { Remove-Item Env:CHATLOG_DIR -ErrorAction SilentlyContinue } else { $env:CHATLOG_DIR = $oldConfigDir }
}

$dataKeyMatch = [regex]::Match($output, '(?m)^Data Key: \[([0-9a-fA-F]{64})\]\s*$')
$imageKeyMatch = [regex]::Match($output, '(?m)^Image Key: \[([0-9a-fA-F]{16,64})?\]\s*$')
if (-not $dataKeyMatch.Success) {
    throw 'No validated data key was returned. Keep WeChat open on the main chat window and retry.'
}

$platformMatch = [regex]::Match($output, '(?m)^Platform: \[(.+)\]\s*$')
$versionMatch = [regex]::Match($output, '(?m)^Version: \[(\d+)\]\s*$')
$fullVersionMatch = [regex]::Match($output, '(?m)^Full Version: \[(.*)\]\s*$')
$dataDirMatch = [regex]::Match($output, '(?m)^Data Dir: \[(.*)\]\s*$')
if (-not $platformMatch.Success -or -not $versionMatch.Success -or -not $dataDirMatch.Success) {
    throw 'Key extraction succeeded, but the WeChat metadata was incomplete.'
}

$dataKey = $dataKeyMatch.Groups[1].Value.ToLowerInvariant()
$imageKey = if ($imageKeyMatch.Success) { $imageKeyMatch.Groups[1].Value.ToLowerInvariant() } else { '' }
try {
    Protect-ChatlogSecret -PlainText $dataKey -Destination $paths.DataSecret
    $imageKeyRefreshed = -not [string]::IsNullOrWhiteSpace($imageKey)
    if ($imageKeyRefreshed) {
        Protect-ChatlogSecret -PlainText $imageKey -Destination $paths.ImgSecret
    } elseif (Test-Path -LiteralPath $paths.ImgSecret) {
        $imageKey = Unprotect-ChatlogSecret -Source $paths.ImgSecret
    }

    $metadata = [ordered]@{
        platform = $platformMatch.Groups[1].Value
        version = [int]$versionMatch.Groups[1].Value
        full_version = $fullVersionMatch.Groups[1].Value
        data_dir = $dataDirMatch.Groups[1].Value
        image_key_available = -not [string]::IsNullOrWhiteSpace($imageKey)
        image_key_refreshed = $imageKeyRefreshed
        saved_at = [DateTimeOffset]::Now.ToString('o')
    }
    [IO.File]::WriteAllText($paths.Metadata, ($metadata | ConvertTo-Json), [Text.UTF8Encoding]::new($false))

    if (-not $Quiet) {
        Write-Host "Data Key:  $dataKey"
        if ($imageKey) {
            Write-Host "Image Key: $imageKey"
        } else {
            Write-Host 'Image Key: not found (text chat remains available; image decoding needs a separately supplied key)'
        }
    }
} finally {
    $dataKey = $null
    $imageKey = $null
    $output = $null
    $dataKeyMatch = $null
    $imageKeyMatch = $null
    [GC]::Collect()
}

Write-Host 'The validated WeChat data key and any available image key were saved with Windows user-scoped DPAPI encryption.'
