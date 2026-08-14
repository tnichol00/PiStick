[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$InstallRoot = Join-Path $env:LOCALAPPDATA 'PiStickServer'
$DataRoot = Join-Path $InstallRoot 'data'
$ConfigPath = Join-Path $DataRoot 'config.json'
$RuntimePath = Join-Path $DataRoot 'runtime.json'
$PythonPathFile = Join-Path $InstallRoot 'pythonw-path.txt'

$Stopped = $false
if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
    try {
        $Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        $Port = if ($Config.port) { [int]$Config.port } else { 8787 }
        if ($Config.shutdown_token) {
            $Headers = @{
                'X-PiStick-Request' = '1'
                'X-PiStick-Shutdown-Token' = [string]$Config.shutdown_token
            }
            Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/admin/shutdown" -Method Post -Headers $Headers -TimeoutSec 3 | Out-Null
            $Stopped = $true
        }
    }
    catch {
        $Stopped = $false
    }
}

if (-not $Stopped -and (Test-Path -LiteralPath $RuntimePath -PathType Leaf)) {
    try {
        $Runtime = Get-Content -LiteralPath $RuntimePath -Raw | ConvertFrom-Json
        $Process = Get-Process -Id ([int]$Runtime.pid) -ErrorAction Stop
        $ExpectedPython = if (Test-Path -LiteralPath $PythonPathFile) {
            (Get-Content -LiteralPath $PythonPathFile -Raw).Trim()
        }
        else { '' }
        if ($ExpectedPython -and $Process.Path -and (
            [System.IO.Path]::GetFullPath($Process.Path) -eq [System.IO.Path]::GetFullPath($ExpectedPython)
        )) {
            Stop-Process -Id $Process.Id -Force
            $Stopped = $true
        }
    }
    catch {
        $Stopped = $false
    }
}

if ($Stopped) {
    Write-Host 'PiStick Server stopped.' -ForegroundColor Green
}
else {
    Write-Host 'PiStick Server is not running.' -ForegroundColor Yellow
}
