# Install PiStick Server for Windows

This edition installs PiStick as a background server that can be opened on the Windows PC or another device connected to the same private Wi-Fi network.

## Requirements

- Windows 10 or Windows 11
- [Python 3.10 or newer](https://www.python.org/downloads/windows/)
- An internet connection
- A free [TMDB account](https://www.themoviedb.org/signup) and its long **API Read Access Token**

## 1. Get the TMDB token

1. Sign in to TMDB.
2. Open [TMDB API settings](https://www.themoviedb.org/settings/api).
3. Request API access if TMDB asks you to complete the form.
4. Copy the long **API Read Access Token**, which normally begins with `eyJ`.

Do not use the shorter v3 API key for the Windows server.

## 2. Install PiStick Server

Open PowerShell normally and paste this entire command:

```powershell
$installer = Join-Path $env:TEMP "install-pistick-server.ps1"
$headers = @{ Accept = "application/vnd.github.raw+json"; "User-Agent" = "PiStick-Installer" }
Invoke-WebRequest -Headers $headers -Uri "https://api.github.com/repos/tnichol00/PiStick/contents/install-server.ps1?ref=agent%2Fwindows-local-server" -OutFile $installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
```

Paste the TMDB API Read Access Token when prompted. The installer places PiStick under `%LOCALAPPDATA%\PiStickServer`, adds Start Menu shortcuts, configures automatic startup, and opens PiStick in the default browser.

If Windows Firewall asks for permission, allow Python on **Private networks** only.

## 3. Open PiStick

- On the server PC, open [http://127.0.0.1:8787](http://127.0.0.1:8787).
- On another device connected to the same Wi-Fi, open the private IP address printed by the installer, such as `http://192.168.1.123:8787`.
- Use the **PiStick Server** Start Menu folder to open or stop the server later.

There is no PiStick network password. Only use this server on a trusted private network.

## Update or repair PiStick Server

Rerun the installation command above. The installer replaces the application files while preserving the TMDB token, profiles, and watch history under `%LOCALAPPDATA%\PiStickServer\data`.
