# Install the PiStick Windows App

## Requirements

- 64-bit Windows 10 or Windows 11
- An internet connection
- A free [TMDB account](https://www.themoviedb.org/signup) and its long **API Read Access Token**

The installer uses Python 3.12. If it is missing, Windows Package Manager installs it automatically. If Windows Package Manager is unavailable, install 64-bit [Python 3.12](https://www.python.org/downloads/windows/) first.

## 1. Get the TMDB token

1. Sign in to TMDB.
2. Open [TMDB API settings](https://www.themoviedb.org/settings/api).
3. Request API access if TMDB asks you to complete the form.
4. Copy the long **API Read Access Token**, which normally begins with `eyJ`.

Do not use the shorter v3 API key for the Windows app.

## 2. Install PiStick

Open PowerShell normally and paste this entire command:

```powershell
$installer = Join-Path $env:TEMP "install-pistick.ps1"
$headers = @{ Accept = "application/vnd.github.raw+json"; "User-Agent" = "PiStick-Installer" }
Invoke-WebRequest -Headers $headers -Uri "https://api.github.com/repos/tnichol00/PiStick/contents/install.ps1?ref=main" -OutFile $installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
```

Paste the TMDB API Read Access Token when prompted. The installer downloads the newest published PiStick release, creates a private runtime under `%LOCALAPPDATA%\PiStick`, and adds **PiStick** to the Start Menu.

## 3. Open PiStick

Open the Start Menu and select **PiStick**.

## Update or repair PiStick

Rerun the installation command above, or run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\PiStick\install.ps1"
```

The installer preserves the saved TMDB token, profiles, and watch history.
