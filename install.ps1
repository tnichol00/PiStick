#requires -Version 5.1
<#
.SYNOPSIS
Installs or updates PiStick for the current Windows user.

.DESCRIPTION
PiStick is installed under %LOCALAPPDATA%\PiStick. Published GitHub Releases
are kept as immutable snapshots, while configuration, profiles, history, and
cache data live outside those snapshots so they survive every update.

The installer creates a PiStick shortcut in the current user's Start Menu. It
does not require administrator rights and does not create an automatic updater.
#>

[CmdletBinding()]
param(
    [switch]$NoLaunch
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$InstallerVersion = '1.0.0'
$Repository = 'tnichol00/PiStick'
$ReleasesApi = "https://api.github.com/repos/$Repository/releases?per_page=100"
$IconFallbackUrl = "https://raw.githubusercontent.com/$Repository/main/assets/pistick.ico"

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA) -or [string]::IsNullOrWhiteSpace($env:APPDATA)) {
    throw 'PiStick requires LOCALAPPDATA and APPDATA for the current Windows user.'
}

$InstallRoot = Join-Path $env:LOCALAPPDATA 'PiStick'
$ReleasesDirectory = Join-Path $InstallRoot 'releases'
$DataDirectory = Join-Path $InstallRoot 'data'
$CacheDirectory = Join-Path $InstallRoot 'cache'
$RuntimesDirectory = Join-Path $InstallRoot 'runtimes'
$CurrentReleaseFile = Join-Path $InstallRoot 'current-release.txt'
$ConfigPath = Join-Path $DataDirectory 'config.json'
$StatePath = Join-Path $DataDirectory 'pistick_state.json'
$IconPath = Join-Path $InstallRoot 'pistick.ico'
$LauncherPath = Join-Path $InstallRoot 'PiStick.vbs'
$InstalledInstallerPath = Join-Path $InstallRoot 'install.ps1'
$StartMenuDirectory = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$ShortcutPath = Join-Path $StartMenuDirectory 'PiStick.lnk'
$TemporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) ("PiStick-Install-" + [guid]::NewGuid().ToString('N'))

function Write-PiStickStep {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "`n[PiStick] $Message" -ForegroundColor Cyan
}

function Write-Utf8File {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )
    & $FilePath @ArgumentList | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit code $LASTEXITCODE)."
    }
}

function Get-PythonExecutable {
    $candidates = @()
    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        $candidates += [pscustomobject]@{ Path = $launcher.Source; Arguments = @('-3') }
    }
    foreach ($version in @('313', '312', '311', '310')) {
        $candidatePath = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$version\python.exe"
        if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
            $candidates += [pscustomobject]@{ Path = $candidatePath; Arguments = @() }
        }
    }
    $python = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        $candidates += [pscustomobject]@{ Path = $python.Source; Arguments = @() }
    }

    $pythonMetadataScript = 'import sys; print(sys.executable); print("SUPPORTED" if sys.version_info >= (3, 10) and sys.version_info < (4, 0) else "UNSUPPORTED")'
    foreach ($candidate in $candidates) {
        try {
            $candidateArguments = @($candidate.Arguments) + @('-c', $pythonMetadataScript)
            $metadata = @(& $candidate.Path @candidateArguments 2>$null)
            if ($LASTEXITCODE -eq 0 -and $metadata.Count -ge 2 -and $metadata[-1].Trim() -eq 'SUPPORTED') {
                $resolved = $metadata[-2].Trim()
                if (Test-Path -LiteralPath $resolved -PathType Leaf) {
                    return $resolved
                }
            }
        }
        catch {
            continue
        }
    }
    return $null
}

function Ensure-Python {
    $pythonPath = Get-PythonExecutable
    if ($null -ne $pythonPath) {
        return $pythonPath
    }

    $winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
    if ($null -eq $winget) {
        throw 'Python 3.10 or newer was not found. Install 64-bit Python from https://www.python.org/downloads/windows/ and run this command again.'
    }

    Write-PiStickStep 'Installing Python 3.12 for the current user'
    Invoke-NativeCommand -FilePath $winget.Source -ArgumentList @(
        'install', '--id', 'Python.Python.3.12', '--exact', '--scope', 'user',
        '--silent', '--accept-package-agreements', '--accept-source-agreements',
        '--disable-interactivity'
    ) -FailureMessage 'Windows Package Manager could not install Python'

    $pythonPath = Get-PythonExecutable
    if ($null -eq $pythonPath) {
        throw 'Python was installed, but its executable could not be located. Open a new PowerShell window and run the PiStick install command again.'
    }
    return $pythonPath
}

function Get-LatestPublishedRelease {
    Write-PiStickStep 'Checking the published PiStick releases'
    $headers = @{
        Accept = 'application/vnd.github+json'
        'X-GitHub-Api-Version' = '2022-11-28'
        'User-Agent' = "PiStick-Windows-Installer/$InstallerVersion"
    }
    try {
        $response = Invoke-RestMethod -Uri $ReleasesApi -Headers $headers -Method Get
    }
    catch {
        $status = $null
        if ($null -ne $_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
        }
        if ($status -eq 403) {
            throw 'GitHub refused the release check, usually because its anonymous API limit was reached. Try again later.'
        }
        throw "The GitHub release check failed: $($_.Exception.Message)"
    }

    $published = @($response | Where-Object {
        -not $_.draft -and $_.published_at -and $_.tag_name -and $_.zipball_url -and $null -ne $_.id
    })
    if ($published.Count -eq 0) {
        throw 'No published PiStick release exists yet.'
    }

    $sortProperties = @(
        @{ Expression = { [datetimeoffset]$_.published_at }; Descending = $true }
        @{ Expression = { [long]$_.id }; Descending = $true }
    )
    $sorted = @($published | Sort-Object -Property $sortProperties)
    return $sorted[0]
}

function Expand-TrustedZip {
    param(
        [Parameter(Mandatory = $true)][string]$ArchivePath,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    $destinationRoot = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
    $destinationPrefix = $destinationRoot + '\'
    $archive = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        foreach ($entry in $archive.Entries) {
            $relative = $entry.FullName.Replace('/', '\')
            if ([string]::IsNullOrWhiteSpace($relative)) {
                continue
            }
            $target = [IO.Path]::GetFullPath((Join-Path $destinationRoot $relative))
            if (-not $target.StartsWith($destinationPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'The release archive contains an unsafe path.'
            }
            if ([string]::IsNullOrEmpty($entry.Name)) {
                [IO.Directory]::CreateDirectory($target) | Out-Null
                continue
            }
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
            $sourceStream = $entry.Open()
            $targetStream = [IO.File]::Open($target, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try {
                $sourceStream.CopyTo($targetStream)
            }
            finally {
                $targetStream.Dispose()
                $sourceStream.Dispose()
            }
        }
    }
    finally {
        $archive.Dispose()
    }
}

function Test-SafeRelativePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([IO.Path]::IsPathRooted($Path) -or $Path -match '(^|[\\/])\.\.([\\/]|$)') {
        return $false
    }
    return -not [string]::IsNullOrWhiteSpace($Path)
}

function Get-ValidatedReleaseSource {
    param(
        [Parameter(Mandatory = $true)]$Release,
        [Parameter(Mandatory = $true)][string]$PythonPath
    )
    $archivePath = Join-Path $TemporaryDirectory 'release.zip'
    $extractPath = Join-Path $TemporaryDirectory 'release'
    Write-PiStickStep "Downloading published release $($Release.tag_name)"
    Invoke-WebRequest -Uri ([string]$Release.zipball_url) -OutFile $archivePath -Headers @{
        Accept = 'application/vnd.github+json'
        'X-GitHub-Api-Version' = '2022-11-28'
        'User-Agent' = "PiStick-Windows-Installer/$InstallerVersion"
    }
    $archiveLength = (Get-Item -LiteralPath $archivePath).Length
    if ($archiveLength -le 0 -or $archiveLength -gt 104857600) {
        throw 'The release archive was empty or unexpectedly large.'
    }
    Expand-TrustedZip -ArchivePath $archivePath -Destination $extractPath

    $entrypoints = @(Get-ChildItem -LiteralPath $extractPath -Filter 'main.py' -File -Recurse | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.Directory.FullName 'pistick-release.json') -PathType Leaf
    })
    if ($entrypoints.Count -ne 1) {
        throw 'The release archive does not contain one valid PiStick application root.'
    }
    $sourceRoot = $entrypoints[0].Directory.FullName
    $manifestPath = Join-Path $sourceRoot 'pistick-release.json'
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    }
    catch {
        throw 'The release manifest is not valid JSON.'
    }
    if ($manifest.installer_schema -ne 1 -or $manifest.entrypoint -ne 'main.py' -or $manifest.updater -ne 'install.sh') {
        throw 'The release manifest is not compatible with this installer.'
    }
    foreach ($relativePath in @($manifest.required_files)) {
        $relativeText = [string]$relativePath
        if (-not (Test-SafeRelativePath -Path $relativeText)) {
            throw "The release manifest contains an unsafe required path: $relativeText"
        }
        if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot $relativeText) -PathType Leaf)) {
            throw "The release is missing required file: $relativeText"
        }
    }
    $windowsRequirements = Join-Path $sourceRoot 'requirements-windows.txt'
    if (-not (Test-Path -LiteralPath $windowsRequirements -PathType Leaf)) {
        throw 'The release does not contain requirements-windows.txt.'
    }
    Get-Content -LiteralPath (Join-Path $sourceRoot 'config.example.json') -Raw | ConvertFrom-Json | Out-Null
    $compileScript = 'import pathlib, sys; [compile(pathlib.Path(path).read_bytes(), path, "exec") for path in sys.argv[1:]]'
    Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @(
        '-c', $compileScript,
        (Join-Path $sourceRoot 'main.py'),
        (Join-Path $sourceRoot 'adblock.py'),
        (Join-Path $sourceRoot 'playback_api.py')
    ) -FailureMessage 'The downloaded release failed Python validation'
    return $sourceRoot
}

function Get-PlainText {
    param([Parameter(Mandatory = $true)][Security.SecureString]$SecureValue)
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Test-TmdbToken {
    param([Parameter(Mandatory = $true)][string]$Token)
    if ($env:PISTICK_SKIP_TMDB_VALIDATION -eq '1') {
        return $true
    }
    try {
        Invoke-RestMethod -Uri 'https://api.themoviedb.org/3/configuration' -Headers @{
            Authorization = "Bearer $Token"
            'User-Agent' = "PiStick-Windows-Installer/$InstallerVersion"
        } -Method Get | Out-Null
        return $true
    }
    catch {
        $status = $null
        if ($null -ne $_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
        }
        if ($status -eq 401 -or $status -eq 403) {
            return $false
        }
        throw "TMDB could not be reached to validate the API Read Access Token: $($_.Exception.Message)"
    }
}

function Copy-LegacyUserData {
    $legacyRoot = Join-Path $env:USERPROFILE 'PiStick'
    if (-not (Test-Path -LiteralPath $ConfigPath) -and (Test-Path -LiteralPath (Join-Path $legacyRoot 'config.json') -PathType Leaf)) {
        try {
            Get-Content -LiteralPath (Join-Path $legacyRoot 'config.json') -Raw | ConvertFrom-Json | Out-Null
            Copy-Item -LiteralPath (Join-Path $legacyRoot 'config.json') -Destination $ConfigPath
            Write-PiStickStep 'Preserved the existing Windows PiStick configuration'
        }
        catch {
            Write-Warning 'The old PiStick config.json was not valid and was not migrated.'
        }
    }
    if (-not (Test-Path -LiteralPath $StatePath) -and (Test-Path -LiteralPath (Join-Path $legacyRoot 'pistick_state.json') -PathType Leaf)) {
        try {
            Get-Content -LiteralPath (Join-Path $legacyRoot 'pistick_state.json') -Raw | ConvertFrom-Json | Out-Null
            Copy-Item -LiteralPath (Join-Path $legacyRoot 'pistick_state.json') -Destination $StatePath
            Write-PiStickStep 'Preserved the existing Windows profiles and watch history'
        }
        catch {
            Write-Warning 'The old PiStick state file was not valid and was not migrated.'
        }
    }
}

function Ensure-PiStickConfig {
    param([Parameter(Mandatory = $true)][string]$ExampleConfigPath)
    $config = $null
    if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
        try {
            $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        }
        catch {
            Write-Warning 'The existing PiStick config.json is invalid. It will be replaced only after a valid token is entered.'
        }
    }
    if ($null -eq $config) {
        $config = Get-Content -LiteralPath $ExampleConfigPath -Raw | ConvertFrom-Json
    }

    $token = ''
    if ($null -ne $config.PSObject.Properties['tmdb_read_token']) {
        $token = [string]$config.tmdb_read_token
    }
    if ([string]::IsNullOrWhiteSpace($token) -and $null -ne $config.PSObject.Properties['tmdb_token']) {
        $token = [string]$config.tmdb_token
    }
    if ($token -like 'PASTE_*') {
        $token = ''
    }
    if ([string]::IsNullOrWhiteSpace($token) -and -not [string]::IsNullOrWhiteSpace($env:PISTICK_TMDB_TOKEN)) {
        $token = $env:PISTICK_TMDB_TOKEN.Trim()
    }

    while ([string]::IsNullOrWhiteSpace($token)) {
        $secureToken = Read-Host 'Paste your TMDB API Read Access Token' -AsSecureString
        $token = (Get-PlainText -SecureValue $secureToken).Trim()
        if ([string]::IsNullOrWhiteSpace($token)) {
            Write-Warning 'The token cannot be empty.'
            continue
        }
        if (-not (Test-TmdbToken -Token $token)) {
            Write-Warning 'TMDB rejected that token. Use the long API Read Access Token, not the shorter v3 key.'
            $token = ''
        }
    }

    if ($null -eq $config.PSObject.Properties['tmdb_read_token']) {
        $config | Add-Member -NotePropertyName 'tmdb_read_token' -NotePropertyValue $token
    }
    else {
        $config.tmdb_read_token = $token
    }
    if ($null -ne $config.PSObject.Properties['tmdb_token']) {
        $config.PSObject.Properties.Remove('tmdb_token')
    }
    if ($null -ne $config.PSObject.Properties['playback_base_url']) {
        $config.PSObject.Properties.Remove('playback_base_url')
    }

    $temporaryConfig = "$ConfigPath.tmp"
    Write-Utf8File -Path $temporaryConfig -Content (($config | ConvertTo-Json -Depth 20) + [Environment]::NewLine)
    Move-Item -LiteralPath $temporaryConfig -Destination $ConfigPath -Force
}

function Install-PiStickIcon {
    param([Parameter(Mandatory = $true)][string]$ReleaseSource)
    $releaseIcon = Join-Path $ReleaseSource 'assets\pistick.ico'
    $temporaryIcon = Join-Path $TemporaryDirectory 'pistick.ico'
    if (Test-Path -LiteralPath $releaseIcon -PathType Leaf) {
        Copy-Item -LiteralPath $releaseIcon -Destination $temporaryIcon -Force
    }
    else {
        Write-PiStickStep 'Downloading the PiStick Start Menu icon'
        Invoke-WebRequest -Uri $IconFallbackUrl -OutFile $temporaryIcon -Headers @{
            'User-Agent' = "PiStick-Windows-Installer/$InstallerVersion"
        }
    }
    $bytes = [IO.File]::ReadAllBytes($temporaryIcon)
    if ($bytes.Length -lt 4 -or $bytes[0] -ne 0 -or $bytes[1] -ne 0 -or $bytes[2] -ne 1 -or $bytes[3] -ne 0) {
        throw 'The PiStick icon is not a valid Windows ICO file.'
    }
    Move-Item -LiteralPath $temporaryIcon -Destination $IconPath -Force
}

function Write-Launcher {
    $launcherSource = @'
Option Explicit
Dim shell, fileSystem, environment, appRoot, pointerFile, pointer, releaseName
Dim releaseDirectory, pythonw, entrypoint, command

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
appRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pointerFile = fileSystem.BuildPath(appRoot, "current-release.txt")

If Not fileSystem.FileExists(pointerFile) Then
    MsgBox "PiStick's active release could not be found. Run the installer again.", 16, "PiStick"
    WScript.Quit 2
End If

Set pointer = fileSystem.OpenTextFile(pointerFile, 1, False)
releaseName = Trim(pointer.ReadAll)
pointer.Close
If Len(releaseName) = 0 Or InStr(releaseName, "\") > 0 Or InStr(releaseName, "/") > 0 Or InStr(releaseName, ":") > 0 Then
    MsgBox "PiStick's active release record is invalid. Run the installer again.", 16, "PiStick"
    WScript.Quit 2
End If

releaseDirectory = fileSystem.BuildPath(fileSystem.BuildPath(appRoot, "releases"), releaseName)
pythonw = fileSystem.BuildPath(fileSystem.BuildPath(fileSystem.BuildPath(fileSystem.BuildPath(appRoot, "runtimes"), releaseName), "Scripts"), "pythonw.exe")
entrypoint = fileSystem.BuildPath(releaseDirectory, "main.py")
If Not fileSystem.FileExists(pythonw) Or Not fileSystem.FileExists(entrypoint) Then
    MsgBox "PiStick is incomplete. Run the installer again.", 16, "PiStick"
    WScript.Quit 2
End If

Set environment = shell.Environment("PROCESS")
environment("PISTICK_CONFIG_PATH") = fileSystem.BuildPath(fileSystem.BuildPath(appRoot, "data"), "config.json")
environment("PISTICK_STATE_PATH") = fileSystem.BuildPath(fileSystem.BuildPath(appRoot, "data"), "pistick_state.json")
environment("PISTICK_CACHE_DIR") = fileSystem.BuildPath(appRoot, "cache")
shell.CurrentDirectory = releaseDirectory
command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & entrypoint & Chr(34)
shell.Run command, 0, False
'@
    $temporaryLauncher = "$LauncherPath.tmp"
    Write-Utf8File -Path $temporaryLauncher -Content ($launcherSource + [Environment]::NewLine)
    Move-Item -LiteralPath $temporaryLauncher -Destination $LauncherPath -Force
}

function Write-StartMenuShortcut {
    [IO.Directory]::CreateDirectory($StartMenuDirectory) | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $shortcut.Arguments = '"' + $LauncherPath + '"'
    $shortcut.WorkingDirectory = $InstallRoot
    $shortcut.IconLocation = "$IconPath,0"
    $shortcut.Description = 'Open PiStick'
    $shortcut.Save()
}

try {
    [IO.Directory]::CreateDirectory($TemporaryDirectory) | Out-Null
    [IO.Directory]::CreateDirectory($InstallRoot) | Out-Null
    [IO.Directory]::CreateDirectory($ReleasesDirectory) | Out-Null
    [IO.Directory]::CreateDirectory($RuntimesDirectory) | Out-Null
    [IO.Directory]::CreateDirectory($DataDirectory) | Out-Null
    [IO.Directory]::CreateDirectory($CacheDirectory) | Out-Null

    $pythonPath = Ensure-Python
    $release = Get-LatestPublishedRelease
    $safeTag = ([string]$release.tag_name -replace '[^A-Za-z0-9._-]', '_').Trim('_')
    if ([string]::IsNullOrWhiteSpace($safeTag)) {
        $safeTag = 'release'
    }
    if ($safeTag.Length -gt 80) {
        $safeTag = $safeTag.Substring(0, 80)
    }
    $releaseName = "$([long]$release.id)-$safeTag"
    $releaseDirectory = Join-Path $ReleasesDirectory $releaseName

    if (-not (Test-Path -LiteralPath $releaseDirectory -PathType Container)) {
        $releaseSource = Get-ValidatedReleaseSource -Release $release -PythonPath $pythonPath
        $stagingRelease = Join-Path $ReleasesDirectory ('.staging-' + [guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($stagingRelease) | Out-Null
        Get-ChildItem -LiteralPath $releaseSource -Force | Copy-Item -Destination $stagingRelease -Recurse -Force
        Move-Item -LiteralPath $stagingRelease -Destination $releaseDirectory
    }
    else {
        Write-PiStickStep "Published release $($release.tag_name) is already downloaded"
    }

    Write-PiStickStep 'Installing the private Python runtime and Windows dependencies'
    $releaseRuntimeDirectory = Join-Path $RuntimesDirectory $releaseName
    $runtimePython = Join-Path $releaseRuntimeDirectory 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
        if (Test-Path -LiteralPath $releaseRuntimeDirectory) {
            Remove-Item -LiteralPath $releaseRuntimeDirectory -Recurse -Force
        }
        Invoke-NativeCommand -FilePath $pythonPath -ArgumentList @('-m', 'venv', $releaseRuntimeDirectory) -FailureMessage 'Python could not create the PiStick runtime'
        Invoke-NativeCommand -FilePath $runtimePython -ArgumentList @(
            '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', '-r',
            (Join-Path $releaseDirectory 'requirements-windows.txt')
        ) -FailureMessage 'PiStick Windows dependencies could not be installed'
    }
    $dependencyCheck = @(
        '-c', 'import pygame, requests; from PySide6.QtWebEngineWidgets import QWebEngineView; from PySide6.QtWebView import QWebView'
    )
    try {
        Invoke-NativeCommand -FilePath $runtimePython -ArgumentList $dependencyCheck -FailureMessage 'PiStick Windows dependencies did not load correctly'
    }
    catch {
        Write-Warning 'The existing PiStick runtime needs repair. Close PiStick if it is currently open.'
        Invoke-NativeCommand -FilePath $runtimePython -ArgumentList @(
            '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', '-r',
            (Join-Path $releaseDirectory 'requirements-windows.txt')
        ) -FailureMessage 'PiStick Windows dependencies could not be repaired'
        Invoke-NativeCommand -FilePath $runtimePython -ArgumentList $dependencyCheck -FailureMessage 'PiStick Windows dependencies did not load correctly after repair'
    }

    Copy-LegacyUserData
    Ensure-PiStickConfig -ExampleConfigPath (Join-Path $releaseDirectory 'config.example.json')
    Install-PiStickIcon -ReleaseSource $releaseDirectory
    Write-Launcher

    $temporaryPointer = "$CurrentReleaseFile.tmp"
    Write-Utf8File -Path $temporaryPointer -Content ($releaseName + [Environment]::NewLine)
    Move-Item -LiteralPath $temporaryPointer -Destination $CurrentReleaseFile -Force

    $releaseInstaller = Join-Path $releaseDirectory 'install.ps1'
    $installerSource = $null
    if (Test-Path -LiteralPath $releaseInstaller -PathType Leaf) {
        $installerSource = $releaseInstaller
    }
    elseif (-not [string]::IsNullOrWhiteSpace($PSCommandPath) -and (Test-Path -LiteralPath $PSCommandPath -PathType Leaf)) {
        $installerSource = $PSCommandPath
    }
    if ($null -ne $installerSource) {
        $sourceFullPath = [IO.Path]::GetFullPath($installerSource)
        $destinationFullPath = [IO.Path]::GetFullPath($InstalledInstallerPath)
        if (-not $sourceFullPath.Equals($destinationFullPath, [StringComparison]::OrdinalIgnoreCase)) {
            Copy-Item -LiteralPath $sourceFullPath -Destination $destinationFullPath -Force
        }
    }
    Write-StartMenuShortcut

    Write-PiStickStep "Installed $($release.tag_name) to $InstallRoot"
    Write-Host 'PiStick is available from the Start Menu.' -ForegroundColor Green
    Write-Host 'Run this installer again whenever you want to check for a published update.'

    if (-not $NoLaunch) {
        Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\wscript.exe') -ArgumentList ('"' + $LauncherPath + '"')
    }
}
finally {
    if (Test-Path -LiteralPath $TemporaryDirectory -PathType Container) {
        Remove-Item -LiteralPath $TemporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}
