[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$InstallRoot = Join-Path $env:LOCALAPPDATA 'PiStickServer'
$ConfigPath = Join-Path $InstallRoot 'data\config.json'
$StartScript = Join-Path $InstallRoot 'app\windows\start-hidden.vbs'

if (-not (Test-Path -LiteralPath $StartScript -PathType Leaf)) {
    throw 'PiStick Server is not installed. Run install-server.ps1 first.'
}

$Port = 8787
if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
    try {
        $Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        if ($Config.port) { $Port = [int]$Config.port }
    }
    catch {
        $Port = 8787
    }
}

$Url = "http://127.0.0.1:$Port/"
$HealthUrl = "http://127.0.0.1:$Port/health"
$Running = $false
try {
    $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
    $Running = [bool]$Health.ok
}
catch {
    $Running = $false
}

if (-not $Running) {
    Start-Process -FilePath "$env:WINDIR\System32\wscript.exe" -ArgumentList @("`"$StartScript`"") -WindowStyle Hidden
    for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
        Start-Sleep -Milliseconds 350
        try {
            $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
            if ($Health.ok) {
                $Running = $true
                break
            }
        }
        catch {
            $Running = $false
        }
    }
}

if (-not $Running) {
    $LogPath = Join-Path $InstallRoot 'logs\server.log'
    throw "PiStick Server could not start. Check $LogPath"
}

Start-Process $Url
