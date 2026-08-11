# PiStick

PiStick is a controller-friendly, Netflix-style TV interface for Raspberry Pi. It uses [TMDB](https://www.themoviedb.org/) for movie and TV metadata, posters, search results, and trailers.

PiStick is currently an alpha. Browsing, profiles, trailers, and watch-state features work, but actual movie and episode playback is still a placeholder until Jellyfin is connected.

## Features

- Netflix-style profiles with separate watch histories
- Featured titles, movies, TV shows, and Continue Watching
- Keyboard, mouse, and controller navigation
- Infinite horizontal discovery carousels
- On-screen keyboard for controller searches
- Movie watch-state tracking
- Season and episode selection with per-profile resume data
- On-demand YouTube trailer screen with fullscreen controls
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

The original Pi Zero W has a single-core ARMv6 processor and 512 MB of RAM. PiStick is tuned for it, but the Chromium-based YouTube trailer player is still demanding. A Pi Zero 2 W or newer model should feel noticeably smoother.

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

### 3. Get the TMDB API Read Access Token

1. Sign in to TMDB.
2. Open [TMDB API settings](https://www.themoviedb.org/settings/api).
3. Complete TMDB's API access form if required.
4. Copy the long **API Read Access Token**.

PiStick uses the long Read Access Token as a Bearer token. Do not use the shorter v3 API key.

### 4. Run the installer

Paste this complete command into the Pi's SSH terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/tnichol00/PiStick/main/install.sh -o /tmp/pistick-install.sh && sudo bash /tmp/pistick-install.sh
```

The installer has one PiStick-specific prompt:

```text
Paste your TMDB API Read Access Token:
```

Paste the token and press Enter. The prompt is hidden, so the token will not appear while it is pasted or typed.

The first installation can take a while on an original Pi Zero W because the Pi must download and install Qt WebEngine and the other system packages. Leave the SSH window open. If the connection is interrupted, reconnect and run the same command again; completed package work and configuration are reused.

When installation succeeds, PiStick starts immediately and launches automatically after every boot.

## What the installer does

The installer automatically:

- Checks that it is running on a supported Raspberry Pi architecture and Debian-based OS.
- Installs Python, Requests, pygame, PyQt5, Qt WebEngine, minimal X11, Matchbox, fonts, graphics libraries, and Bluetooth support.
- Validates the TMDB API Read Access Token before saving it.
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
5. Keeps the user's TMDB token, profiles, watch history, and caches.
6. Automatically returns to the previous release if startup fails.

The updater never installs a normal tag, branch commit, or draft release.

## Persistent files

These files survive every update:

| Purpose | Path |
| --- | --- |
| TMDB configuration | `/etc/pistick/config.json` |
| Profiles and watch history | `/var/lib/pistick/user-data.json` |
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

Rerun the full installation command. It safely reuses the existing TMDB token and release data while restoring the updater command.

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

## Creating a release

PiStick installs only published GitHub Releases. Maintainers should release from a tested `main` commit:

1. Run the repository checks:

   ```bash
   bash -n install.sh
   bash tests/test_installer.sh
   python3 -m py_compile main.py
   python3 -m json.tool config.example.json >/dev/null
   python3 -m json.tool pistick-release.json >/dev/null
   ```

2. Confirm the release contains:
   - `main.py`
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
- Profiles and watch history stay on the Pi.
- The TMDB token is stored at `/etc/pistick/config.json` with restricted permissions.
- The installer does not collect or store a GitHub credential.
- A real `config.json` must never be committed or included in a release.

This product uses the TMDB API but is not endorsed or certified by TMDB.
