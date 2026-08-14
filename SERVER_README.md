# PiStick Server for Windows

This branch runs PiStick as a web app on your local Wi-Fi network instead of opening a PyQt desktop window.

## What this edition changes

- Runs silently in the Windows background and starts when the user signs in.
- Opens in any browser on the same PC or another device connected to the same local network.
- Saves profiles, watch history, episode progress, and exact resume timestamps in the server data folder.
- Keeps the TMDB Read Access Token on the server; browser JavaScript never receives it.
- Preserves the Netflix-style home rows, profiles, search, movies, TV seasons, episode picker, Continue Watching, and Videasy playback.
- Supports keyboard and basic Gamepad API navigation in the browser.

The server binds to `0.0.0.0` by default so phones, TVs, and other computers on the same Wi-Fi can connect. There is no login or network password: anyone who can reach the PC on port 8787 can use PiStick and change its profiles and watch data.

Videasy playback uses an unsandboxed outer iframe because its player refuses to
run when the iframe has a `sandbox` attribute. Only the allow-listed Videasy
HTTPS player URL can use that path. Normal browser cross-origin isolation keeps
the player from reading the PiStick app, and state-changing server requests
still require PiStick's private request header.

## Install on Windows

Open PowerShell and run:

```powershell
$installer = Join-Path $env:TEMP "install-pistick-server.ps1"
$headers = @{ Accept = "application/vnd.github.raw+json"; "User-Agent" = "PiStick-Installer" }
Invoke-WebRequest -Headers $headers -Uri "https://api.github.com/repos/tnichol00/PiStick/contents/install-server.ps1?ref=agent%2Fwindows-local-server" -OutFile $installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
```

The installer:

1. Finds Python 3.10 or newer already installed on Windows.
2. Downloads this server branch.
3. Installs it under `%LOCALAPPDATA%\PiStickServer`.
4. Imports the existing desktop PiStick profiles and history when available.
5. Adds a per-user Windows Startup shortcut.
6. Starts the hidden LAN server and opens it in the default browser.

Use the **PiStick Server** Start Menu folder to open or stop the server later.

## Run from source

```powershell
python server.py --open
```

The console prints both the address for this PC and an address such as
`http://192.168.1.123:8787/` for other devices. On the first run, allow Python
through Windows Firewall on **Private networks** when Windows asks.

No additional Python packages are required for the server edition.

## Private data

Installed data stays outside the downloaded application code:

```text
%LOCALAPPDATA%\PiStickServer\data\config.json
%LOCALAPPDATA%\PiStickServer\data\state.json
%LOCALAPPDATA%\PiStickServer\data\cache\
%LOCALAPPDATA%\PiStickServer\logs\server.log
```

Updating the app does not replace this folder.
