Set-StrictMode -Version Latest

function Get-ChatlogStackHome {
    if ($env:WECHAT_CHATLOG_HOME) {
        return [IO.Path]::GetFullPath($env:WECHAT_CHATLOG_HOME)
    }
    return Join-Path $env:LOCALAPPDATA 'WeChatChatlogStack'
}

function Get-ChatlogStackPaths {
    $stackRoot = Get-ChatlogStackHome
    return [ordered]@{
        Home       = $stackRoot
        Bin        = Join-Path $stackRoot 'bin'
        Core       = Join-Path $stackRoot 'bin\chatlog-core.exe'
        Config     = Join-Path $stackRoot 'config'
        Metadata   = Join-Path $stackRoot 'config\wechat.json'
        Secrets    = Join-Path $stackRoot 'secrets'
        DataSecret = Join-Path $stackRoot 'secrets\data-key.dpapi'
        ImgSecret  = Join-Path $stackRoot 'secrets\image-key.dpapi'
        CoreState  = Join-Path $stackRoot 'core-state'
        Work       = Join-Path $stackRoot 'decrypted'
        Logs       = Join-Path $stackRoot 'logs'
        Source     = Join-Path $stackRoot 'source\chatlog-eb9572e'
    }
}

function Initialize-ChatlogStackDirectories {
    $paths = Get-ChatlogStackPaths
    foreach ($path in @($paths.Home, $paths.Bin, $paths.Config, $paths.Secrets, $paths.CoreState, $paths.Work, $paths.Logs)) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
    return $paths
}

function Protect-ChatlogSecret {
    param(
        [Parameter(Mandatory)] [string] $PlainText,
        [Parameter(Mandatory)] [string] $Destination
    )

    $secure = ConvertTo-SecureString -String $PlainText -AsPlainText -Force
    try {
        $encrypted = ConvertFrom-SecureString -SecureString $secure
        [IO.File]::WriteAllText($Destination, $encrypted, [Text.UTF8Encoding]::new($false))
    } finally {
        $secure.Dispose()
        $encrypted = $null
    }
}

function Unprotect-ChatlogSecret {
    param([Parameter(Mandatory)] [string] $Source)

    $encrypted = [IO.File]::ReadAllText($Source, [Text.Encoding]::UTF8).Trim()
    $secure = ConvertTo-SecureString -String $encrypted
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        $secure.Dispose()
        $encrypted = $null
    }
}

function Get-ChatlogMetadata {
    $paths = Get-ChatlogStackPaths
    if (-not (Test-Path -LiteralPath $paths.Metadata)) {
        throw 'WeChat metadata is missing. Run scripts\save-wechat-keys.ps1 while WeChat is open.'
    }
    return Get-Content -LiteralPath $paths.Metadata -Raw | ConvertFrom-Json
}

function Test-LocalHttp {
    param([Parameter(Mandatory)] [string] $Url)
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}
