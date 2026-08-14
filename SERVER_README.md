# PiStick Server for Windows

This branch runs PiStick as a private web app at `http://127.0.0.1:8787` instead of opening a PyQt desktop window.

## What this edition changes

- Runs silently in the Windows background and starts when the user signs in.
- Opens in any browser on the same PC through localhost only.
- Saves profiles, watch history, episode progress, and exact resume timestamps in the server data folder.
- Keeps the TMDB Read Access Token on the server; browser JavaScript never receives it.
- Preserves the Netflix-style home rows, profiles, search, movies, TV seasons, episode picker, Continue Watching, and Videasy playback.
- Supports keyboard and basic Gamepad API navigation in the browser.

The server deliberately binds only to `127.0.0.1`. Other computers on the network cannot connect to it.

Videasy playback uses an unsandboxed outer iframe because its player refuses to
run when the iframe has a `sandbox` attribute. Only the allow-listed Videasy
HTTPS player URL can use that path. Normal browser cross-origin isolation keeps
the player from reading the localhost app, and state-changing server requests
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
6. Starts the hidden localhost server and opens it in the default browser.

Use the **PiStick Server** Start Menu folder to open or stop the server later.

## Run from source

```powershell
python server.py --open
```

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
