# PiStick

> **Original Raspberry Pi Zero W edition:** this `agent/pi-zero-w` branch replaces the broken Linux installer with a real Raspberry Pi OS Legacy Lite (32-bit Bookworm) appliance setup. It runs the lightweight PiStick server in fullscreen Chromium, starts at boot, includes low-memory tuning, HDMI-only Wi-Fi/controller settings, and optional unauthenticated access at `http://pistick.local`. Follow [PI_ZERO_W_README.md](PI_ZERO_W_README.md) for installation and controller pairing.

> **Windows localhost-server edition:** the `agent/windows-local-server` branch runs PiStick silently in the Windows background and serves the interface at `http://127.0.0.1:8787`. See [SERVER_README.md](SERVER_README.md) for its installer and usage guide. The original desktop/Raspberry Pi application remains unchanged on `main`.

> **Standalone iPhone and iPad edition:** the `agent/ios-standalone` branch contains a universal on-device app with no Python process or PiStick server. It includes a Loop-style GitHub Actions/TestFlight build. See [ios/README.md](ios/README.md) for setup and installation.

<img src="assets/pistick-icon.png" alt="PiStick logo" width="128">

PiStick is a controller-friendly, Netflix-style TV interface for Raspberry Pi and Windows. It uses [TMDB](https://www.themoviedb.org/) for movie and TV metadata, posters, search results, and trailers, then opens movies and episodes through [Videasy's documented player API](https://www.videasy.to/docs).

PiStick is currently an alpha. Browsing, profiles, trailers, watch-state features, and embedded movie and episode playback are implemented. Compatible embed pages report playback time to PiStick, which saves and restores an exact per-profile resume timestamp.

## Features

- Netflix-style profiles with separate watch histories
- Featured titles, movies, TV shows, and immediately refreshed Continue Watching
- Keyboard, mouse, and controller navigation
- Infinite horizontal discovery carousels
- On-screen keyboard for controller searches
- Movie, show, and individual-episode watch-state controls
- Season and episode selection with per-profile resume data
- Videasy movie and episode playback from TMDB-number-based URLs
- Client-side 1080p HLS selection with the best available fallback
- Strong playback-only ad, tracker, adult-site, pop-up, and redirect blocking
- Autoplaying Videasy and YouTube players with controller subtitle controls
- Low-memory mode for the original Raspberry Pi Zero W
- Optional access from other devices on the same Wi-Fi
- HDMI-only Wi-Fi, Bluetooth, wired-controller, and LAN controls
- Manual installation with branch-scoped Pi updates and release-scoped Windows updates

## What you need

For either platform:

- Free [TMDB account](https://www.themoviedb.org/signup)
- Internet connection
- Optional Bluetooth or USB controller

For Raspberry Pi:

- Raspberry Pi Zero W or a newer Raspberry Pi
- 8 GB or larger microSD card
- Reliable 5 V power supply
- Mini-HDMI cable for an original Pi Zero W when connecting it to a TV
- 2.4 GHz Wi-Fi for an original Pi Zero W
- A computer with a microSD-card reader for Raspberry Pi Imager
- Micro-USB OTG adapter when using a USB controller with an original Pi Zero W

For Windows:

- 64-bit Windows 10 or Windows 11
- Microsoft Edge WebView2 Runtime; Windows 11 normally includes it

The original Pi Zero W has a single-core ARMv6 processor and 512 MB of RAM. PiStick is tuned for it, but Chromium-based trailer and movie playback is still demanding. A Pi Zero 2 W or newer model should feel noticeably smoother.

## Videasy playback

The playback URL helpers live in `playback_api.py` and are called by `main.py` like this:

```python
from playback_api import getmovie, getshow

movie_url = getmovie(550)
episode_url = getshow(1399, 1, 3)
```

The calls produce Videasy's documented movie and TV paths:

```text
https://player.videasy.to/movie/550
https://player.videasy.to/tv/1399/1/3
```

Videasy's docs currently show `player.videasy.net`, which redirects to `player.videasy.to`. PiStick uses the final HTTPS origin directly so its anti-popup navigation lock does not reject that redirect. When saved progress exists, PiStick appends only Videasy's documented `progress` parameter, such as `?progress=120`.

On this Pi Zero W branch, PiStick loads the player inside the fullscreen Raspberry Pi OS Chromium package. This avoids Qt WebEngine and uses the browser's normal media-codec and Gamepad API support. Movies open from **Watch Movie**; TV shows first open the season and episode picker. Controller A toggles playback, X requests English subtitles, Left/Right seeks, and B closes the player.

Videasy sends `PLAYER_EVENT` progress messages containing the current timestamp and duration. The PiStick web interface accepts those documented messages with origin checks and saves the reported position per profile.

The native Windows WebView2 path can directly read a top-level HTML5 `<video>` element and accepts the same `pistick-playback-progress` messages. If a Windows embed keeps its video inside a different-origin nested iframe, that frame must post progress itself; PiStick does not disable browser security or read through the origin boundary.

A custom player can also expose its playback state in one of these ways:

- Define `window.pistickGetPlaybackState()` and return `{currentTime, duration}`.
- Use a top-level HTML5 `<video>` element; PiStick reads it automatically.
- From a nested player iframe, post `{type: "pistick-playback-progress", currentTime, duration}` to the top window.

For a custom player that does not expose an HTML5 `<video>` element, define `window.pistickSeekTo(seconds)` so PiStick can apply the saved resume position without changing the API URL. It can also define `window.pistickSeekBy(offsetSeconds)` for controller skipping, `window.pistickSetQuality(label, height)` for the preferred resolution, and `window.pistickSetSubtitles(enabled, language)` for PiStick's English subtitle toggle.

PiStick requests 1080p from YouTube trailers and locks an exposed HLS player to its 1080p rendition. If a particular source has no 1080p rendition, PiStick selects the closest available rendition, preferring the highest one below 1080p. This is done inside the loaded player and does not add anything to the documented API URL.

PiStick reads the latest reported state every two seconds. The timestamp and duration are stored beside that movie or episode's existing Continue Watching record in the private per-profile state file. When playback closes, the Home page's Continue Watching row is rebuilt immediately—even if Home is currently behind Search or the title-details dialog. When that title is reopened, the bridge seeks to the saved timestamp after its video metadata becomes available. The values are not stored beside secrets in `config.json`.

An in-progress TV show exposes separate **Mark Episode as Finished** and **Mark Show as Finished** actions. Finishing the whole show removes it from Continue Watching and replaces those actions with the same **Mark as Unwatched** action used for finished movies.

Do not use `document.domain`, `window.parent.addEventListener(...)`, or direct reads from `iframe.contentWindow.document` to connect frames. Modern Chromium keeps different origins isolated. Register listeners on the current frame's own `window` and exchange data with `window.top.postMessage(...)` instead.

### Playback sandbox

On this Pi branch, every third-party player is placed in a restricted cross-origin iframe. Pop-up windows, downloads, payment requests, and top-level navigation are not granted. The full desktop and Windows editions have additional engine-specific host filtering; those filters are not claimed by this lightweight Chromium edition.

## Install PiStick

The Pi installer follows only `agent/pi-zero-w`. The Windows installer uses its separate Windows release process.

### Get the required TMDB token

1. Sign in to TMDB.
2. Open [TMDB API settings](https://www.themoviedb.org/settings/api).
3. Complete TMDB's API access form if required.
4. Copy the long **API Read Access Token**.

PiStick uses the long Read Access Token as a Bearer token. Do not use the shorter v3 API key. Both installers ask for this token the first time and preserve it during updates.

### Linux / Raspberry Pi command

This branch's installer is intended for the original Pi Zero W on **Raspberry Pi OS Legacy Lite (32-bit, Bookworm)**. Follow the separate [Pi Zero W installation and controller guide](PI_ZERO_W_README.md). The installation command is:

```bash
curl -fsSL https://raw.githubusercontent.com/tnichol00/PiStick/refs/heads/agent/pi-zero-w/install.sh -o /tmp/pistick-install.sh && sudo bash /tmp/pistick-install.sh
```

The installer saves private configuration under `/var/lib/pistick`, installs PiStick under `/opt/pistick`, starts it immediately, and launches it automatically after every boot. Other devices on the same Wi-Fi can open `http://pistick.local` while LAN access is enabled.

### Windows Donwload:
Go to releases and download PiStick.exe

### Raspberry Pi OS setup details

PiStick is designed for a fresh **Raspberry Pi OS Legacy Lite (32-bit, Bookworm)** installation. It adds only the graphical components needed to run the app, not a normal desktop, taskbar, file manager, or desktop icons.

The finished boot flow is:

```text
Power on -> Raspberry Pi OS Lite -> minimal X11 session -> PiStick fullscreen
```

#### 1. Write Raspberry Pi OS to the microSD card

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer.
2. Insert the microSD card. Everything currently on the selected card will be erased.
3. In Imager, choose your Raspberry Pi model.
4. Under **Raspberry Pi OS (other)**, choose **Raspberry Pi OS (Legacy) Lite (32-bit)** and confirm it says Debian Bookworm. Do not choose Trixie or a 64-bit image for an original Pi Zero W.
5. Open OS customization and set:
   - Hostname: `pistick`
   - Username: `pistick`
   - A secure password
   - Your Wi-Fi name and password
   - Your Wi-Fi country
   - SSH enabled with password authentication
6. Write the card, insert it into the Pi, and power the Pi on.

Allow several minutes for the first boot. An original Pi Zero W can connect only to a 2.4 GHz Wi-Fi network. Raspberry Pi's [headless setup guide](https://www.raspberrypi.com/documentation/computers/getting-started.html#headless-remote-setup) also explains the Imager and SSH options.

#### 2. Connect to the Pi over SSH

On Windows, open PowerShell. On macOS or Linux, open Terminal. Run:

```bash
ssh pistick@pistick.local
```

Accept the host-key prompt if this is the first connection, then enter the password chosen in Imager.

If `pistick.local` does not resolve, find the Pi's IP address in your router's connected-device list and connect with that address instead:

```bash
ssh pistick@192.168.1.123
```

Replace `192.168.1.123` with the Pi's actual address.

#### 3. Run the Linux installer

After copying the TMDB token, run the Linux command shown above:

```bash
curl -fsSL https://raw.githubusercontent.com/tnichol00/PiStick/refs/heads/agent/pi-zero-w/install.sh -o /tmp/pistick-install.sh && sudo bash /tmp/pistick-install.sh
```

The installer prompts for:

```text
Paste your TMDB API Read Access Token:
```

Paste the token and press Enter. The prompt is hidden, so the value does not appear while it is pasted or typed. It is stored only in `/var/lib/pistick/data/config.json` on the Pi.

The first installation can take a while on an original Pi Zero W because the Pi must download Chromium and the minimal display packages. Leave the SSH window open. If the connection is interrupted, reconnect and run the same command again.

When installation succeeds, PiStick starts immediately and launches automatically after every boot.

## What the installers do

Both installers:

- Validate the TMDB API Read Access Token before saving it.
- Keep private configuration, profiles, history, and caches outside release folders.
- Preserve existing user data when the installer is run again.
- Avoid timers, scheduled tasks, cron jobs, automatic update checks, and `git pull` workflows.

The Linux installer also:

- Checks that it is running on a supported Raspberry Pi architecture and Debian-based OS.
- Installs Python, Chromium, minimal X11, Openbox, fonts, NetworkManager, mDNS, and Bluetooth support.
- Downloads the dedicated `agent/pi-zero-w` branch and validates its required files and syntax.
- Stores immutable release snapshots under `/opt/pistick/releases/`.
- Points `/opt/pistick/current` at the active release.
- Creates a minimal fullscreen X11 service with no desktop.
- Starts PiStick at boot and restarts it if it crashes.
- Installs the manual `pistick-update` command.
- Installs HDMI-only Wi-Fi and controller controls plus the SSH-only `pistick-configure-tmdb` command.
- Keeps application releases separate from private configuration and watch data.

The Windows installer also:

- Installs per-user files under `%LOCALAPPDATA%\PiStick`.
- Uses an isolated virtual environment under the PiStick installation instead of changing system Python packages.
- Creates a stable hidden-window launcher plus a Start Menu shortcut and multi-resolution icon.
- Keeps each downloaded app release under `%LOCALAPPDATA%\PiStick\releases` and switches the active release only after validation and dependency installation succeed.

## Update PiStick

Updates remain manual on both platforms.

On Linux, connect over SSH and run:

```bash
sudo pistick-update
```

On Windows, rerun the Windows installation command, or run the saved installer directly:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\PiStick\install.ps1"
```

The Pi updater downloads the newest `agent/pi-zero-w` commit and safely reuses private user data. The Windows updater continues to use the Windows release process.

When a new Pi branch commit exists, the updater:

1. Downloads the branch installer and source without changing private data.
2. Validates its required files and syntax.
3. Activates a new versioned application snapshot.
4. Restarts the services and checks the local server's health.
5. Keeps the user's TMDB token, profiles, watch history, and caches.

The Pi updater intentionally follows only the dedicated Pi Zero W branch.

## Persistent files

These files survive every update:

| Purpose | Linux / Raspberry Pi | Windows |
| --- | --- | --- |
| Private TMDB configuration | `/var/lib/pistick/data/config.json` | `%LOCALAPPDATA%\PiStick\data\config.json` |
| Profiles, watch history, and resume timestamps | `/var/lib/pistick/data/state.json` | `%LOCALAPPDATA%\PiStick\data\pistick_state.json` |
| Application releases | `/opt/pistick/releases/` | `%LOCALAPPDATA%\PiStick\releases\` |
| Posters, API data, and browser cache | `/var/lib/pistick/data/cache/` and `/var/cache/pistick/` | `%LOCALAPPDATA%\PiStick\cache\` |

The Linux service and Windows launcher pass these locations to the app through `PISTICK_CONFIG_PATH`, `PISTICK_STATE_PATH`, and `PISTICK_CACHE_DIR`.

If an older manual installation exists at `/home/USERNAME/PiStick` on Linux or `%USERPROFILE%\PiStick` on Windows, the first installer run attempts to preserve a valid `config.json` and `pistick_state.json` automatically.

## Service controls and logs

Check whether PiStick is running:

```bash
sudo systemctl status pistick-server.service pistick-kiosk.service
```

Follow live logs:

```bash
journalctl -u pistick-server.service -u pistick-kiosk.service -f
```

Show logs from the current boot:

```bash
journalctl -u pistick-server.service -u pistick-kiosk.service -b --no-pager
```

Restart or stop PiStick:

```bash
sudo systemctl restart pistick-server.service pistick-kiosk.service
sudo systemctl stop pistick-kiosk.service
```

## HDMI settings and LAN access

On the Pi's **Who's watching?** screen, **Settings** appears beside **Manage Profiles**. It can connect to a new 2.4 GHz Wi-Fi network, pair a Bluetooth controller, show wired controllers, and turn access for other devices on or off.

While LAN access is enabled, phones, tablets, and computers on the same Wi-Fi can open:

```text
http://pistick.local
```

There is no login. Remote devices can use the ordinary PiStick interface and share its profiles and watch history, but they cannot open or call the system settings. The server verifies that Wi-Fi, Bluetooth, and LAN-toggle requests originate from the Pi itself.

The TMDB token is also unavailable through the browser. Change it over SSH with:

```bash
sudo pistick-configure-tmdb
```

## Controller setup

A USB controller can be connected through a micro-USB OTG adapter. To pair Bluetooth from HDMI, put the controller in pairing mode, open **Settings**, select **Pair new controller**, then select the controller and choose **Pair**.

If the HDMI scan cannot find it, connect over SSH and run:

```bash
bluetoothctl
```

Then enter:

```text
power on
agent on
default-agent
scan on
```

Put the controller into pairing mode. When its address appears, replace `XX:XX:XX:XX:XX:XX` below with that address:

```text
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
quit
```

Restart PiStick after pairing:

```bash
sudo systemctl restart pistick-kiosk.service
```

Controller playback controls:

- A: play/pause trailers, movies, and episodes; movie/episode playback also reveals the full player control bar
- X: toggle English subtitles; subtitles start off whenever a player opens
- Left/Right on the D-pad or left stick: seek 10 seconds backward/forward in a movie or episode
- B or keyboard Escape during movie or episode playback: close playback and return directly to title details
- B during trailer fullscreen: return to the trailer window; press B again to close it
- Keyboard Escape follows the same two-step back behavior for trailers

## Optional remote viewing

SSH is the normal maintenance method. To temporarily see and control PiStick without a normal desktop, install `x11vnc`:

```bash
sudo apt update
sudo apt install -y x11vnc
mkdir -p ~/.vnc
x11vnc -storepasswd ~/.vnc/passwd
x11vnc -display :0 -auth guess -forever -shared -rfbauth ~/.vnc/passwd
```

Connect a VNC viewer to `pistick.local:5900`. Keep the SSH window open while `x11vnc` is running, and press Ctrl+C when finished.

VNC adds CPU and network overhead. It is useful for checking that the app opens, but it is not an accurate test of carousel animation or trailer performance.

## Memory and swap

Check current RAM and swap use with:

```bash
free -h
swapon --show
```

Current Raspberry Pi OS releases configure swap differently from older releases, so do not blindly follow old instructions that edit `/etc/dphys-swapfile`. Increase swap only if logs show an out-of-memory failure. Swap may prevent a crash, but it is much slower than RAM and cannot make trailer playback smooth.

## Troubleshooting

### The install command returns `Could not resolve host`

The Pi is not reaching the internet or DNS is not ready. Check:

```bash
ping -c 3 github.com
```

Confirm that the original Pi Zero W is using a 2.4 GHz network.

### GitHub returns HTTP `403`

GitHub may have temporarily rate-limited an anonymous branch download. Wait and run the installer again later. PiStick does not request or store a GitHub credential.

### The Pi branch cannot be downloaded

Confirm the branch URL in [PI_ZERO_W_README.md](PI_ZERO_W_README.md), then check that the Pi can reach GitHub with `ping -c 3 github.com`.

### `pistick-update: command not found`

Rerun the full installation command. It safely reuses the existing private configuration and release data while restoring the updater command.

### A PiStick service repeatedly restarts or stays failed

Read the latest error:

```bash
journalctl -u pistick-server.service -u pistick-kiosk.service -b -n 150 --no-pager
```

Then check the installed server and browser:

```bash
python3 -c "import pistick_server; print('Server OK')"
command -v chromium-browser || command -v chromium
```

### The TMDB setup message remains visible

Validate the private configuration file:

```bash
sudo python3 -m json.tool /var/lib/pistick/data/config.json
```

Then restart PiStick:

```bash
sudo systemctl restart pistick-server.service pistick-kiosk.service
```

If the token was rejected or copied incorrectly, replace it through the SSH-only configuration command:

```bash
sudo pistick-configure-tmdb
```

This does not remove profiles or watch history.

### A paired controller is not detected

Check whether Linux created a controller device:

```bash
ls -l /dev/input/js* /dev/input/by-id/*joystick* 2>/dev/null
```

If no device appears, reconnect or pair the controller again. If a device appears but PiStick still cannot see it, reboot once so the service user receives all updated device-group permissions.

### Trailer playback is slow

The trailer screen uses Chromium and is the heaviest part of this Pi edition. An original Pi Zero W may still struggle with YouTube playback even when the rest of the interface is responsive.

### A movie or episode does not load

First validate the local configuration and confirm the TMDB token file is valid JSON:

```bash
sudo python3 -m json.tool /var/lib/pistick/data/config.json
```

Then test Videasy's player from the Pi:

```bash
curl -I https://player.videasy.to/movie/550
```

The player needs JavaScript and media playback support from the browser engine. Check the PiStick logs for Chromium, network, or certificate errors:

```bash
journalctl -u pistick-kiosk.service -b -n 100 --no-pager
```

No Videasy API key is required. The TMDB token is used only for title metadata; Videasy movie or episode paths are built from the selected TMDB number, season, and episode.

### The log shows iframe or cross-origin JavaScript warnings

These messages come from the embed page rather than the TMDB request:

- `document.domain mutation is ignored` means the page still uses the obsolete `document.domain` workaround. Remove that assignment.
- `Allow attribute will take precedence over 'allowfullscreen'` means an iframe contains both attributes. Use `allow="autoplay; encrypted-media; fullscreen"` and remove the separate `allowfullscreen` attribute.
- `Blocked a frame ... Protocols, domains, and ports must match` means code is directly accessing a parent or child frame from a different origin. Use `postMessage()`; matching only the domain name is insufficient when the scheme, subdomain, or port differs.

PiStick's browser interface uses origin-checked `postMessage()` events. If third-party player code prints one of these messages, that provider code must be corrected for the warning itself to disappear.

### Windows says `HLS not supported`

PiStick uses Edge WebView2 on Windows because Qt WebEngine cannot add H.264/AAC support after it has been built. Rerun the installed Windows updater to repair the isolated dependencies:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\PiStick\install.ps1"
```

If the message remains, install Microsoft's current [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) and reopen PiStick from the Start Menu. Do not add `--disable-web-security`: that does not add missing codecs and would weaken origin protections. A subtitle CORS error or source-fetch error is emitted by Videasy or one of its upstream services, not by PiStick's movie/TV URL builder.

## Creating a Windows release

The Windows installer uses published GitHub Releases. The Pi Zero W updater follows
`agent/pi-zero-w` directly. Maintainers should create Windows releases from a tested
`main` commit:

1. Run the repository checks:

   ```bash
   bash -n install.sh
   bash tests/test_installer.sh
   python3 -m py_compile main.py adblock.py playback_api.py
   python3 -m unittest discover -s tests -p 'test_*.py'
   python3 -m json.tool config.example.json >/dev/null
   python3 -m json.tool pistick-release.json >/dev/null
   ```

2. Confirm the release contains:
   - `main.py`
   - `adblock.py`
   - `playback_api.py`
   - `config.example.json`
   - `install.sh`
   - `install.ps1`
   - `requirements-windows.txt`
   - `assets/pistick.ico`
   - `pistick-release.json`
3. In GitHub, open **Releases** and choose **Draft a new release**.
4. Create a version tag such as `v0.1.1-alpha` or `v1.0.0` targeting the tested `main` commit.
5. Add release notes and publish it. It may be marked as a pre-release, but it cannot remain a draft.

GitHub's automatic source archive is enough; no separate ZIP asset is required. The installer downloads the archive referenced by the published release.

## Data and privacy

- TMDB metadata and posters come from TMDB.
- Trailers play through YouTube.
- Selecting playback sends the TMDB title number—and, for TV, the season and episode numbers—to Videasy. A saved resume timestamp is also sent through Videasy's documented `progress` query parameter.
- The playback ad blocker refreshes its public OISD hosts list at most once every 24 hours. That request contains no title, playback URL, profile, or watch-history data. The normalized list is cached locally; private block/allow domains stay in the private configuration.
- Profiles and watch history stay on the installed device.
- The TMDB token is stored in the platform-specific private configuration path listed above.
- Pi LAN access has no authentication. Anyone on the same Wi-Fi who opens the PiStick address can use its profiles and alter ordinary watch state, but system settings and TMDB-token changes remain local/SSH-only.
- The installer does not collect or store a GitHub credential.
- The repository's `config.example.json` contains only placeholders. A real `config.json` must never be committed or included in a release.

This product uses the TMDB API but is not endorsed or certified by TMDB.
Only use PiStick and third-party playback services for content you are authorized to access.
