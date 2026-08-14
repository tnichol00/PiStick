[CmdletBinding()]
param(
    [string]$TmdbToken = '',
    [string]$PythonPath = '',
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Repository = 'tnichol00/PiStick'
$Branch = 'agent/windows-local-server'
$ArchiveUrl = "https://api.github.com/repos/$Repository/zipball/$([Uri]::EscapeDataString($Branch))"
$InstallRoot = Join-Path $env:LOCALAPPDATA 'PiStickServer'
$DataRoot = Join-Path $InstallRoot 'data'
$ConfigPath = Join-Path $DataRoot 'config.json'
$AppRoot = Join-Path $InstallRoot 'app'
$TemporaryRoot = Join-Path $env:TEMP ("PiStickServer-" + [Guid]::NewGuid().ToString('N'))
$PowerShellPath = (Get-Process -Id $PID).Path

function Test-PiStickPython([string]$Executable) {
    if ([string]::IsNullOrWhiteSpace($Executable)) { return $null }
    try {
        $ResolvedPath = [System.IO.Path]::GetFullPath($Executable.Trim())
        if (-not (Test-Path -LiteralPath $ResolvedPath -PathType Leaf)) { return $null }
        $VersionOutput = & $ResolvedPath --version 2>&1 | Select-Object -Last 1
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$VersionOutput)) { return $null }
        $VersionText = ([string]$VersionOutput).Trim()
        if ($VersionText -notmatch '^Python\s+([0-9]+)\.([0-9]+)\.([0-9]+)') { return $null }
        [int]$Major = $Matches[1]
        [int]$Minor = $Matches[2]
        [int]$Micro = $Matches[3]
        if ($Major -ne 3 -or $Minor -lt 10) { return $null }
        return [PSCustomObject]@{
            Path = $ResolvedPath
            Version = "$Major.$Minor.$Micro"
        }
    }
    catch {
        return $null
    }
}

function Resolve-PiStickPython([string]$PreferredPath) {
    $Candidate = Test-PiStickPython $PreferredPath
    if ($Candidate) { return $Candidate }

    $PythonCommand = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($PythonCommand) {
        $Candidate = Test-PiStickPython $PythonCommand.Source
        if ($Candidate) { return $Candidate }
    }

    $PyLauncher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        try {
            $LaunchedPath = & $PyLauncher.Source -3 -c 'import sys; print(sys.executable)' 2>$null | Select-Object -Last 1
            if ($LASTEXITCODE -eq 0) {
                $Candidate = Test-PiStickPython ([string]$LaunchedPath)
                if ($Candidate) { return $Candidate }
            }
        }
        catch { }
    }

    $Python3Command = Get-Command 'python3.exe' -ErrorAction SilentlyContinue
    if ($Python3Command) {
        $Candidate = Test-PiStickPython $Python3Command.Source
        if ($Candidate) { return $Candidate }
    }

    $LocalPythonRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path -LiteralPath $LocalPythonRoot -PathType Container) {
        $InstalledPythons = Get-ChildItem -Path (Join-Path $LocalPythonRoot 'Python*\python.exe') -File -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending
        foreach ($InstalledPython in $InstalledPythons) {
            $Candidate = Test-PiStickPython $InstalledPython.FullName
            if ($Candidate) { return $Candidate }
        }
    }

    throw 'Python 3.10 or newer could not be started. Run python --version, or rerun this installer with -PythonPath followed by the full path to python.exe.'
}

function Write-Utf8NoBom([string]$Path, [string]$Value) {
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Value, $Encoding)
}

function Read-PrivateToken {
    Write-Host ''
    Write-Host 'Paste your TMDB API Read Access Token.' -ForegroundColor Cyan
    Write-Host 'Press Enter without typing anything to add it later in the PiStick Settings screen.' -ForegroundColor DarkGray
    $Secure = Read-Host -AsSecureString 'TMDB token'
    $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    }
}

function Test-TmdbToken([string]$Token) {
    if ([string]::IsNullOrWhiteSpace($Token)) { return }
    if ($Token.Length -lt 24) { throw 'That does not look like the long TMDB API Read Access Token.' }
    $Headers = @{ Authorization = "Bearer $Token"; Accept = 'application/json' }
    try {
        Invoke-RestMethod -Uri 'https://api.themoviedb.org/3/configuration' -Headers $Headers -TimeoutSec 20 | Out-Null
    }
    catch {
        throw 'TMDB rejected that token. Copy the API Read Access Token, not the shorter v3 API key.'
    }
}

function New-Shortcut([string]$Path, [string]$Target, [string]$Arguments, [string]$WorkingDirectory, [string]$Icon) {
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($Path)
    $Shortcut.TargetPath = $Target
    $Shortcut.Arguments = $Arguments
    $Shortcut.WorkingDirectory = $WorkingDirectory
    if ($Icon -and (Test-Path -LiteralPath $Icon -PathType Leaf)) {
        $Shortcut.IconLocation = "$Icon,0"
    }
    $Shortcut.Save()
}

Write-Host 'Installing PiStick Server...' -ForegroundColor Cyan
$Python = Resolve-PiStickPython $PythonPath
Write-Host "Using Python $($Python.Version)" -ForegroundColor DarkGray

New-Item -ItemType Directory -Path $TemporaryRoot -Force | Out-Null
try {
    $ArchivePath = Join-Path $TemporaryRoot 'source.zip'
    $ExtractRoot = Join-Path $TemporaryRoot 'source'
    $Headers = @{ 'User-Agent' = 'PiStick-Server-Installer'; Accept = 'application/vnd.github+json' }
    Invoke-WebRequest -Uri $ArchiveUrl -Headers $Headers -OutFile $ArchivePath -UseBasicParsing
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $ExtractRoot -Force
    $SourceRoot = Get-ChildItem -LiteralPath $ExtractRoot -Directory | Select-Object -First 1
    if (-not $SourceRoot -or -not (Test-Path -LiteralPath (Join-Path $SourceRoot.FullName 'server.py'))) {
        throw 'The downloaded server source is incomplete.'
    }

    $StagedApp = Join-Path $TemporaryRoot 'app'
    New-Item -ItemType Directory -Path $StagedApp -Force | Out-Null
    $RequiredItems = @('server.py', 'playback_api.py', 'pistick_server', 'windows')
    foreach ($Item in $RequiredItems) {
        $Source = Join-Path $SourceRoot.FullName $Item
        if (-not (Test-Path -LiteralPath $Source)) { throw "Required server file is missing: $Item" }
        Copy-Item -LiteralPath $Source -Destination $StagedApp -Recurse -Force
    }
    $AssetSource = Join-Path $SourceRoot.FullName 'assets\pistick.ico'
    $AssetDestination = Join-Path $StagedApp 'assets'
    New-Item -ItemType Directory -Path $AssetDestination -Force | Out-Null
    Copy-Item -LiteralPath $AssetSource -Destination (Join-Path $AssetDestination 'pistick.ico') -Force

    & $Python.Path -m compileall -q $StagedApp
    if ($LASTEXITCODE -ne 0) { throw 'The downloaded server failed Python validation.' }

    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null

    $ExistingConfig = $null
    if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
        try { $ExistingConfig = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json } catch { $ExistingConfig = $null }
    }
    $ExistingToken = if ($ExistingConfig -and $ExistingConfig.tmdb_read_token) { [string]$ExistingConfig.tmdb_read_token } else { '' }
    if ([string]::IsNullOrWhiteSpace($TmdbToken) -and [string]::IsNullOrWhiteSpace($ExistingToken)) {
        $TmdbToken = Read-PrivateToken
    }
    if (-not [string]::IsNullOrWhiteSpace($TmdbToken)) {
        Test-TmdbToken $TmdbToken
        $SavedToken = $TmdbToken.Trim()
    }
    else {
        $SavedToken = $ExistingToken
    }
    $Port = if ($ExistingConfig -and $ExistingConfig.port) { [int]$ExistingConfig.port } else { 8787 }
    $Config = [ordered]@{ tmdb_read_token = $SavedToken; port = $Port }
    if ($ExistingConfig -and $ExistingConfig.shutdown_token) {
        $Config.shutdown_token = [string]$ExistingConfig.shutdown_token
    }
    Write-Utf8NoBom $ConfigPath (($Config | ConvertTo-Json) + [Environment]::NewLine)

    $StatePath = Join-Path $DataRoot 'state.json'
    $LegacyState = Join-Path $env:LOCALAPPDATA 'PiStick\data\pistick_state.json'
    if (-not (Test-Path -LiteralPath $StatePath) -and (Test-Path -LiteralPath $LegacyState -PathType Leaf)) {
        Copy-Item -LiteralPath $LegacyState -Destination $StatePath
        Write-Host 'Imported profiles and watch history from PiStick.' -ForegroundColor Green
    }

    if (Test-Path -LiteralPath (Join-Path $AppRoot 'windows\stop-server.ps1') -PathType Leaf) {
        try {
            & $PowerShellPath -NoProfile -ExecutionPolicy Bypass -File (Join-Path $AppRoot 'windows\stop-server.ps1') | Out-Null
            Start-Sleep -Milliseconds 700
        }
        catch { }
    }
    $PreviousApp = Join-Path $InstallRoot 'app.previous'
    if (Test-Path -LiteralPath $PreviousApp) { Remove-Item -LiteralPath $PreviousApp -Recurse -Force }
    if (Test-Path -LiteralPath $AppRoot) { Move-Item -LiteralPath $AppRoot -Destination $PreviousApp }
    Move-Item -LiteralPath $StagedApp -Destination $AppRoot

    $PythonDirectory = Split-Path -Parent $Python.Path
    $Pythonw = Join-Path $PythonDirectory 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $Pythonw -PathType Leaf)) { $Pythonw = $Python.Path }
    Write-Utf8NoBom (Join-Path $InstallRoot 'pythonw-path.txt') ($Pythonw + [Environment]::NewLine)

    $IconPath = Join-Path $AppRoot 'assets\pistick.ico'
    $StartScript = Join-Path $AppRoot 'windows\start-hidden.vbs'
    $OpenScript = Join-Path $AppRoot 'windows\open-server.ps1'
    $StopScript = Join-Path $AppRoot 'windows\stop-server.ps1'
    $StartupDirectory = [Environment]::GetFolderPath('Startup')
    $StartMenuDirectory = Join-Path ([Environment]::GetFolderPath('Programs')) 'PiStick Server'
    New-Item -ItemType Directory -Path $StartMenuDirectory -Force | Out-Null

    New-Shortcut (Join-Path $StartupDirectory 'PiStick Server Background.lnk') "$env:WINDIR\System32\wscript.exe" "`"$StartScript`"" $AppRoot $IconPath
    New-Shortcut (Join-Path $StartMenuDirectory 'Open PiStick Server.lnk') $PowerShellPath "-NoProfile -ExecutionPolicy Bypass -File `"$OpenScript`"" $AppRoot $IconPath
    New-Shortcut (Join-Path $StartMenuDirectory 'Stop PiStick Server.lnk') $PowerShellPath "-NoProfile -ExecutionPolicy Bypass -File `"$StopScript`"" $AppRoot $IconPath

    Start-Process -FilePath "$env:WINDIR\System32\wscript.exe" -ArgumentList @("`"$StartScript`"") -WindowStyle Hidden
    $HealthUrl = "http://127.0.0.1:$Port/health"
    $Started = $false
    for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
        Start-Sleep -Milliseconds 350
        try {
            $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
            if ($Health.ok) { $Started = $true; break }
        }
        catch { $Started = $false }
    }
    if (-not $Started) {
        throw "PiStick Server installed but did not start. Check $(Join-Path $InstallRoot 'logs\server.log')"
    }

    Write-Host ''
    Write-Host 'PiStick Server is installed and running in the background.' -ForegroundColor Green
    Write-Host "Open it at http://127.0.0.1:$Port/" -ForegroundColor Cyan
    Write-Host 'It will start automatically when you sign in to Windows.' -ForegroundColor DarkGray
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port/" }
}
finally {
    if (Test-Path -LiteralPath $TemporaryRoot) {
        Remove-Item -LiteralPath $TemporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
