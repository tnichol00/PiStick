# PiStick

PiStick is a controller-friendly, Netflix-style TV interface for Raspberry Pi. It uses [TMDB](https://www.themoviedb.org/) for movie and TV metadata, posters, search results, and trailers, then opens movies and episodes through a user-configured legal playback service.

PiStick is currently an alpha. Browsing, profiles, trailers, watch-state features, and embedded movie and episode playback are implemented. Compatible embed pages report playback time to PiStick, which saves and restores an exact per-profile resume timestamp.

## Features

- Netflix-style profiles with separate watch histories
- Featured titles, movies, TV shows, and immediately refreshed Continue Watching
- Keyboard, mouse, and controller navigation
- Infinite horizontal discovery carousels
- On-screen keyboard for controller searches
- Movie watch-state tracking
- Season and episode selection with per-profile resume data
- Embedded movie and episode playback from TMDB-number-based URLs
- Client-side 1080p HLS selection with the best available fallback
- Playback-only ad, tracker, pop-up, and redirect blocking
- On-demand YouTube trailer screen with controller play/pause
- Low-memory mode for the original Raspberry Pi Zero W
- Manual, release-only installation and updates with rollback

## What you need

- Raspberry Pi Zero W or a newer Raspberry Pi
- 8 GB or larger microSD card
- Reliable 5 V power supply
- Mini-HDMI cable for an original Pi Zero W when connecting it to a TV
- 2.4 GHz Wi-Fi for an original Pi Zero W
- A computer with a microSD-card reader for Raspberry Pi Imager
- Optional Bluetooth controller or USB controller with a micro-USB OTG adapter
- Free [TMDB account](https://www.themoviedb.org/signup)
- The HTTPS base URL of a legal playback service you control or are authorized to use

The original Pi Zero W has a single-core ARMv6 processor and 512 MB of RAM. PiStick is tuned for it, but Chromium-based trailer and movie playback is still demanding. A Pi Zero 2 W or newer model should feel noticeably smoother.

## Playback API

The playback URL helpers live in `playback_api.py` and are called by `main.py` like this:

```python
from playback_api import getmovie, getshow

movie_url = getmovie(550)
episode_url = getshow(1399, 1, 3)
```

The service's base URL is read at runtime from the private `playback_base_url` field in `/etc/pistick/config.json`. The tracked example contains only a placeholder. With a locally configured base URL of `https://playback.example`, the calls produce:

```text
https://playback.example/embed/movie/550
https://playback.example/embed/tv/1399/1/3
```

These are the complete requests. PiStick does not append undocumented playback-provider query parameters. The repository and published releases never contain the real playback host. On Raspberry Pi, PiStick loads the resulting page in Qt WebEngine. On a Windows test PC, movie and episode playback uses Windows' Edge WebView2 engine so H.264/AAC HLS streams are not limited by Qt WebEngine's build-time codec selection. Movies open from **Watch Movie**. TV shows first open the season and episode picker, then the selected episode opens in the same embedded player. The player expands fullscreen after opening. In either player, controller A toggles play/pause. During movie or episode playback, controller Left/Right seeks backward/forward 10 seconds, and B closes playback directly back to the title details screen.

When saved progress exists, PiStick first opens the same documented endpoint and then sends the saved timestamp to the embedded player. Qt WebEngine on the Pi injects a small frame-local bridge into the top page and every nested frame. An ordinary HTML5 `<video>` element is detected in whichever frame owns it, receives the resume position locally, and relays its progress to the top page with `window.postMessage()`—PiStick never reads a cross-origin frame's DOM from its parent.

The native Windows WebView2 path can directly read a top-level HTML5 `<video>` element and accepts the same `pistick-playback-progress` messages. If a Windows embed keeps its video inside a different-origin nested iframe, that frame must post progress itself; PiStick does not disable browser security or read through the origin boundary.

A custom player can also expose its playback state in one of these ways:

- Define `window.pistickGetPlaybackState()` and return `{currentTime, duration}`.
- Use a top-level HTML5 `<video>` element; PiStick reads it automatically.
- From a nested player iframe, post `{type: "pistick-playback-progress", currentTime, duration}` to the top window.

For a custom player that does not expose an HTML5 `<video>` element, define `window.pistickSeekTo(seconds)` so PiStick can apply the saved resume position without changing the API URL. It can also define `window.pistickSeekBy(offsetSeconds)` for controller skipping and `window.pistickSetQuality(label, height)` for the preferred resolution.

PiStick requests 1080p from YouTube trailers and locks an exposed HLS player to its 1080p rendition. If a particular source has no 1080p rendition, PiStick selects the closest available rendition, preferring the highest one below 1080p. This is done inside the loaded player and does not add anything to the documented API URL.

PiStick reads the latest reported state every two seconds. The timestamp and duration are stored beside that movie or episode's existing Continue Watching record in the private per-profile state file. When playback closes, the Home page's Continue Watching row is rebuilt immediately—even if Home is currently behind Search or the title-details dialog. When that title is reopened, the bridge seeks to the saved timestamp after its video metadata becomes available. The values are not stored beside secrets in `config.json`.

Do not use `document.domain`, `window.parent.addEventListener(...)`, or direct reads from `iframe.contentWindow.document` to connect frames. Modern Chromium keeps different origins isolated. Register listeners on the current frame's own `window` and exchange data with `window.top.postMessage(...)` instead.

### Playback ad blocker

Ad blocking is enabled by default only for movie and episode playback. YouTube trailers keep their separate, unfiltered browser profile so a playback rule cannot break trailers.

On Raspberry Pi, the Qt WebEngine profile blocks known ad and tracker hosts before requests reach Chromium, rejects pop-up windows and off-origin top-level redirects, and injects cosmetic filtering into every frame. On Windows, Edge WebView2 keeps its built-in tracking protection enabled while PiStick blocks pop-ups, late `fetch`/XHR/beacon ad requests, ad elements, and attempts to replace the API page with an off-origin redirect. PiStick does not disable browser security, proxy HTTPS traffic, or broadly block URL paths that might contain video, HLS, or subtitle data.

The built-in host list is intentionally conservative. Extra domains can be added privately without changing tracked code:

```json
{
  "adblock_enabled": true,
  "adblock_domains": [
    "ads.example",
    "*.popups.example"
  ]
}
```

Add only hostnames, without `https://`, a path, or a query. The installer preserves these fields during updates and rollback. Set `adblock_enabled` to `false` if a playback provider requires a blocked host.

### Testing playback on Windows

Windows playback requires Python 3.10 or newer, PySide6 6.11 or newer, and the Microsoft Edge WebView2 Runtime. Windows 11 normally includes WebView2. From the PiStick folder, run:

```powershell
py -m pip install --upgrade -r requirements-windows.txt
py main.py
```

PiStick continues to use Qt WebEngine for YouTube trailers, but uses Edge WebView2 for movies and episodes. If the native player is unavailable, the playback screen shows the missing requirement instead of opening the known codec-limited Qt WebEngine fallback.

## Install PiStick

PiStick is designed for a fresh **Raspberry Pi OS Lite (32-bit)** installation. It adds only the graphical components needed to run the app, not a normal desktop, taskbar, file manager, or desktop icons.

The finished boot flow is:

```text
Power on -> Raspberry Pi OS Lite -> minimal X11 session -> PiStick fullscreen
```

### 1. Write Raspberry Pi OS to the microSD card

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer.
2. Insert the microSD card. Everything currently on the selected card will be erased.
3. In Imager, choose your Raspberry Pi model.
4. Choose **Raspberry Pi OS Lite (32-bit)**. Do not choose a 64-bit image for an original Pi Zero W.
5. Open OS customization and set:
   - Hostname: `pistick`
   - Username: `pistick`
   - A secure password
   - Your Wi-Fi name and password
   - Your Wi-Fi country
   - SSH enabled with password authentication
6. Write the card, insert it into the Pi, and power the Pi on.

Allow several minutes for the first boot. An original Pi Zero W can connect only to a 2.4 GHz Wi-Fi network. Raspberry Pi's [headless setup guide](https://www.raspberrypi.com/documentation/computers/getting-started.html#headless-remote-setup) also explains the Imager and SSH options.

### 2. Connect to the Pi over SSH

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

### 3. Get the required configuration

1. Sign in to TMDB.
2. Open [TMDB API settings](https://www.themoviedb.org/settings/api).
3. Complete TMDB's API access form if required.
4. Copy the long **API Read Access Token**.

PiStick uses the long Read Access Token as a Bearer token. Do not use the shorter v3 API key.

Also have the base URL for your legal playback service ready. It must begin with `https://` and must not include `/embed/movie/...`, `/embed/tv/...`, credentials, a query, or a fragment.

### 4. Run the installer

Paste this complete command into the Pi's SSH terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/tnichol00/PiStick/main/install.sh -o /tmp/pistick-install.sh && sudo bash /tmp/pistick-install.sh
```

The installer has two PiStick-specific prompts:

```text
Paste your TMDB API Read Access Token:
Paste your legal playback service base URL:
```

Paste each value and press Enter. Both prompts are hidden, so neither value appears while it is pasted or typed. They are stored only in `/etc/pistick/config.json` on the Pi.

The first installation can take a while on an original Pi Zero W because the Pi must download and install Qt WebEngine and the other system packages. Leave the SSH window open. If the connection is interrupted, reconnect and run the same command again; completed package work and configuration are reused.

When installation succeeds, PiStick starts immediately and launches automatically after every boot.

## What the installer does

The installer automatically:

- Checks that it is running on a supported Raspberry Pi architecture and Debian-based OS.
- Installs Python, Requests, pygame, PyQt5, Qt WebEngine, minimal X11, Matchbox, fonts, graphics libraries, and Bluetooth support.
- Validates the TMDB API Read Access Token before saving it.
- Validates and normalizes the private HTTPS playback base URL before saving it.
- Downloads the newest published PiStick GitHub Release, never an unfinished branch commit.
- Ignores draft releases. Published pre-releases are eligible for installation.
- Validates the release manifest, required files, Bash syntax, and Python syntax.
- Stores immutable release snapshots under `/opt/pistick/releases/`.
- Points `/opt/pistick/current` at the active release.
- Stores configuration, profiles, history, and caches outside the release directory.
- Creates a minimal fullscreen X11 service with no desktop.
- Starts PiStick at boot and restarts it if it crashes.
- Installs the manual `pistick-update` command.
- Preserves the previous release and rolls back if a new release fails its startup check.

The installer does **not** install a timer, cron job, automatic update checker, or `git pull` workflow.

## Update PiStick

Updates happen only when someone connects over SSH and runs:

```bash
sudo pistick-update
```

The updater checks the published [PiStick Releases](https://github.com/tnichol00/PiStick/releases), selects the most recently published non-draft release, and does nothing if that release is already installed.

When a new release exists, the updater:

1. Downloads it without changing the running release.
2. Validates its required files and syntax.
3. Stops PiStick only after validation succeeds.
4. Activates the new release and watches its startup health.
5. Keeps the user's TMDB token, playback base URL, profiles, watch history, and caches.
6. Automatically returns to the previous release if startup fails.

The updater never installs a normal tag, branch commit, or draft release.

## Persistent files

These files survive every update:

| Purpose | Path |
| --- | --- |
| Private TMDB and playback configuration | `/etc/pistick/config.json` |
| Profiles, watch history, and resume timestamps | `/var/lib/pistick/user-data.json` |
| Installed release record | `/var/lib/pistick/installed-release.json` |
| Posters, API data, and WebEngine cache | `/var/cache/pistick/` |

The service passes these locations to the app through `PISTICK_CONFIG_PATH`, `PISTICK_STATE_PATH`, and `PISTICK_CACHE_DIR`.

If an older manual installation exists at `/home/USERNAME/PiStick`, the first installer run attempts to preserve a valid `config.json` and `pistick_state.json` automatically.

## Service controls and logs

Check whether PiStick is running:

```bash
sudo systemctl status pistick.service
```

Follow live logs:

```bash
journalctl -u pistick.service -f
```

Show logs from the current boot:

```bash
journalctl -u pistick.service -b --no-pager
```

Restart or stop PiStick:

```bash
sudo systemctl restart pistick.service
sudo systemctl stop pistick.service
```

## Controller setup

A USB controller can be connected through a micro-USB OTG adapter. To pair a Bluetooth controller, connect over SSH and run:

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
sudo systemctl restart pistick.service
```

Controller playback controls:

- A: play/pause trailers, movies, and episodes
- Left/Right on the D-pad or left stick: seek 10 seconds backward/forward in a movie or episode
- B during movie or episode fullscreen: close playback and return directly to title details
- B during trailer fullscreen: return to the trailer window; press B again to close it

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

GitHub may have temporarily rate-limited anonymous release checks. Wait and run the installer again later. PiStick does not request or store a GitHub credential.

### `No published PiStick release exists yet`

The installer found no non-draft GitHub Release. A tag or branch by itself is intentionally not installable. Check the [Releases page](https://github.com/tnichol00/PiStick/releases) and try again once a release is published.

### `pistick-update: command not found`

Rerun the full installation command. It safely reuses the existing private configuration and release data while restoring the updater command.

### `pistick.service` repeatedly restarts or stays failed

Read the latest error:

```bash
journalctl -u pistick.service -b -n 100 --no-pager
```

Then check the installed dependencies:

```bash
python3 -c "import requests, pygame; from PyQt5.QtWebEngineWidgets import QWebEngineView; print('Dependencies OK')"
```

### The TMDB setup screen remains visible

Validate the private configuration file:

```bash
sudo python3 -m json.tool /etc/pistick/config.json
```

Then restart PiStick:

```bash
sudo systemctl restart pistick.service
```

If the token was rejected or copied incorrectly, remove only the saved configuration and rerun the installer:

```bash
sudo rm /etc/pistick/config.json
curl -fsSL https://raw.githubusercontent.com/tnichol00/PiStick/main/install.sh -o /tmp/pistick-install.sh && sudo bash /tmp/pistick-install.sh
```

This does not remove profiles or watch history.

### A paired controller is not detected

Check whether Linux created a controller device:

```bash
ls -l /dev/input/js* /dev/input/by-id/*joystick* 2>/dev/null
```

If no device appears, reconnect or pair the controller again. If a device appears but PiStick still cannot see it, reboot once so the service user receives all updated device-group permissions.

### Trailer playback is slow

The trailer screen uses Qt WebEngine and Chromium, making it the heaviest part of PiStick. It is created only after **Watch Trailer** is selected and destroyed after closing. An original Pi Zero W may still struggle with YouTube playback even when the rest of the interface is responsive.

### A movie or episode does not load

First validate the local configuration and confirm that `playback_base_url` is present:

```bash
sudo python3 -m json.tool /etc/pistick/config.json
```

Then test that configured host from the Pi, replacing the placeholder below with the private value shown in the configuration:

```bash
curl -I https://YOUR-LEGAL-PLAYBACK-HOST.example/
```

The player needs JavaScript and media playback support from the browser engine. Check the PiStick logs for Chromium, network, or certificate errors:

```bash
journalctl -u pistick.service -b -n 100 --no-pager
```

No playback API key is required. The TMDB token is used only for title metadata; `playback_base_url` supplies the private host, and movie or episode paths are built from the selected TMDB number, season, and episode.

### The log shows iframe or cross-origin JavaScript warnings

These messages come from the embed page rather than the TMDB request:

- `document.domain mutation is ignored` means the page still uses the obsolete `document.domain` workaround. Remove that assignment.
- `Allow attribute will take precedence over 'allowfullscreen'` means an iframe contains both attributes. Use `allow="autoplay; encrypted-media; fullscreen"` and remove the separate `allowfullscreen` attribute.
- `Blocked a frame ... Protocols, domains, and ports must match` means code is directly accessing a parent or child frame from a different origin. Use `postMessage()`; matching only the domain name is insufficient when the scheme, subdomain, or port differs.

PiStick's Qt WebEngine progress bridge follows this model and runs separately inside every frame. If third-party player code prints one of these messages, that provider code must be corrected for the warning itself to disappear.

### The Windows log says `HLS not supported`

Qt WebEngine cannot enable H.264/AAC after it has been built. Update the Windows test dependencies and restart PiStick so movie and episode playback uses Edge WebView2:

```powershell
py -m pip install --upgrade -r requirements-windows.txt
```

Do not add `--disable-web-security`: that does not add missing codecs and would weaken origin protections. A subtitle CORS error, `Fresh source error`, or Tailwind production warning is emitted by the hosted page or its upstream services, not by PiStick's documented `/embed/...` URL builder.

## Creating a release

PiStick installs only published GitHub Releases. Maintainers should release from a tested `main` commit:

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
   - `pistick-release.json`
3. In GitHub, open **Releases** and choose **Draft a new release**.
4. Create a version tag such as `v0.1.1-alpha` or `v1.0.0` targeting the tested `main` commit.
5. Add release notes and publish it. It may be marked as a pre-release, but it cannot remain a draft.

GitHub's automatic source archive is enough; no separate ZIP asset is required. The installer downloads the archive referenced by the published release.

## Data and privacy

- TMDB metadata and posters come from TMDB.
- Trailers play through YouTube.
- Selecting playback sends the TMDB title number—and, for TV, the season and episode numbers—to the configured playback host.
- The playback ad blocker uses a bundled host list and optional domains stored only in the private local configuration; it does not download or report browsing data to a filtering service.
- Profiles and watch history stay on the Pi.
- The TMDB token and playback base URL are stored at `/etc/pistick/config.json` with restricted permissions.
- The installer does not collect or store a GitHub credential.
- The repository's `config.example.json` contains only placeholders. A real `config.json` must never be committed or included in a release.

This product uses the TMDB API but is not endorsed or certified by TMDB.
