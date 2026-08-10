# PiStick

PiStick is a controller-friendly, Netflix-style media interface designed to turn a Raspberry Pi connected to a TV into a lightweight streaming appliance.

The app currently uses [TMDB](https://www.themoviedb.org/) for movie and TV metadata, posters, search results, and trailers. Jellyfin playback is planned; the current `watch_title()` function is a placeholder for that integration.

## Current features

- Netflix-style profiles with separate watch histories
- Featured titles, movies, TV shows, and Continue Watching
- Keyboard, mouse, and controller navigation
- Infinite horizontal discovery carousels
- In-app YouTube trailers with fullscreen controls
- Movie watch-state tracking
- Season and episode picker with per-profile episode resume
- On-screen keyboard for controller searches
- Low-memory tuning for the original Raspberry Pi Zero W
- Release-only installation and updates with automatic rollback

## Hardware requirements

- Raspberry Pi Zero W or newer Raspberry Pi
- 8 GB or larger microSD card
- Stable 5 V power supply
- Mini-HDMI cable for final TV use
- 2.4 GHz Wi-Fi for the original Pi Zero W
- Optional Bluetooth controller or USB controller with a micro-USB OTG adapter

The original Pi Zero W has a single-core ARMv6 processor and 512 MB of RAM. PiStick is optimized for it, but YouTube's Chromium-based trailer player remains demanding. A Pi Zero 2 W or newer model will provide a noticeably smoother experience.

# Before distributing PiStick

The installer deliberately uses GitHub without a GitHub token so the user's only installer input is their TMDB API Read Access Token. Before the public install command can work, the repository owner must:

1. Merge the `installer` branch into `main`.
2. Make `tnichol00/PiStick` public.
3. Publish at least one non-draft GitHub Release from a commit containing:
   - `main.py`
   - `config.example.json`
   - `install.sh`
   - `pistick-release.json`
   - The external configuration/state path support in `main.py`

A Git tag by itself is not enough. The tag must be attached to a published GitHub Release. Published pre-releases are eligible; draft releases are ignored.

# Dedicated Raspberry Pi installation

The installer is designed for a fresh **Raspberry Pi OS Lite (32-bit)** system accessed over SSH. It installs a minimal X11 layer for Qt, but it does not install a desktop, taskbar, file manager, or desktop icons.

The final boot flow is:

```text
Power on -> Raspberry Pi OS Lite -> minimal X11 -> PiStick
```

## 1. Prepare the microSD card

Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on another computer.

In Imager:

1. Choose your Raspberry Pi model.
2. Select **Raspberry Pi OS Lite (32-bit)**. Do not select the 64-bit image for an original Pi Zero W.
3. Choose the microSD card.
4. Open OS customization and set:
   - Hostname: `pistick`
   - Username: `pistick`
   - A secure password
   - Your Wi-Fi name and password
   - Wi-Fi country
   - Enable SSH with password authentication
5. Write the card, insert it into the Pi, and power it on.

Allow several minutes for the first boot. The original Pi Zero W only supports 2.4 GHz Wi-Fi.

## 2. Connect over SSH

From Windows PowerShell:

```powershell
ssh pistick@pistick.local
```

If `pistick.local` does not resolve, find the Pi's IP address in your router's connected-device list and use it instead:

```powershell
ssh pistick@192.168.1.123
```

## 3. Run the installer

After the `installer` branch has been merged into `main`, run this single command on the Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/tnichol00/PiStick/main/install.sh -o /tmp/pistick-install.sh && sudo bash /tmp/pistick-install.sh
```

The installer asks for only one PiStick setting:

```text
Paste your TMDB API Read Access Token:
```

Create or sign into a [TMDB account](https://www.themoviedb.org/signup), open [TMDB API settings](https://www.themoviedb.org/settings/api), and paste the long **API Read Access Token**. The installer validates it before continuing. The shorter v3 API key is not accepted by PiStick's current Bearer-token configuration.

The installation can take a while on the original Pi Zero W. When it finishes, PiStick starts automatically and will launch again on every boot.

## What the installer does

The installer automatically:

- Installs Python, PyQt5, QtWebEngine, pygame, Requests, X11, Matchbox, fonts, graphics libraries, and Bluetooth support.
- Creates a minimal no-desktop X11 kiosk service.
- Gives the PiStick service user access to display and controller devices.
- Downloads the newest published GitHub Release, never a branch commit.
- Compiles and validates the release before activation.
- Stores every release in `/opt/pistick/releases/`.
- Atomically points `/opt/pistick/current` at the active release.
- Keeps the TMDB token, profiles, watch history, and cache outside release folders.
- Starts PiStick automatically at boot and restarts it if the app crashes.
- Installs the manual `pistick-update` command.
- Keeps the previous working release and automatically rolls back if an update fails its startup check.

The installer does **not** install an update timer, cron job, or background release checker.

# Updating PiStick

Updates happen only when someone connects over SSH and runs:

```bash
sudo pistick-update
```

That command:

1. Queries GitHub's published Releases API.
2. Ignores drafts and normal un-released tags.
3. Selects the newest published release, including a published pre-release.
4. Does nothing if that release is already installed.
5. Downloads and validates the release archive.
6. Stops PiStick only when a new release is ready.
7. Switches to the new release and watches for startup crashes.
8. Rolls back to the previous release if the health check fails.

It never runs `git pull`, never tracks `main`, and never installs an unpublished commit.

# Persistent files

Updates never replace these files:

| Purpose | Path |
| --- | --- |
| TMDB configuration | `/etc/pistick/config.json` |
| Profiles and watch history | `/var/lib/pistick/user-data.json` |
| Installed release record | `/var/lib/pistick/installed-release.json` |
| Posters, API data, and WebEngine cache | `/var/cache/pistick/` |

The service reads those paths through `PISTICK_CONFIG_PATH`, `PISTICK_STATE_PATH`, and `PISTICK_CACHE_DIR`. Release directories remain read-only and contain application code only.

If the old manual `/home/USERNAME/PiStick` installation exists, the first installer run migrates a valid `config.json` and `pistick_state.json` automatically.

# Service controls and logs

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

Restart or stop the app:

```bash
sudo systemctl restart pistick.service
sudo systemctl stop pistick.service
```

# Controller setup

USB controllers can be connected through a powered micro-USB OTG adapter. To pair a Bluetooth controller, connect over SSH and run:

```bash
bluetoothctl
```

Then run these commands inside `bluetoothctl`:

```text
power on
agent on
default-agent
scan on
```

When the controller's address appears, replace `XX:XX:XX:XX:XX:XX` below:

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

# Optional remote viewing

SSH is the normal maintenance method. If PiStick's X11 session is running, you can temporarily install `x11vnc` and view the interface remotely:

```bash
sudo apt update
sudo apt install -y x11vnc
mkdir -p ~/.vnc
x11vnc -storepasswd ~/.vnc/passwd
x11vnc -display :0 -auth guess -forever -shared -rfbauth ~/.vnc/passwd
```

Connect a VNC viewer to `pistick.local:5900`. VNC consumes extra CPU and can make animations look slower than they will over HDMI, so do not use it for final performance measurements.

# Optional swap increase

QtWebEngine can briefly use a large amount of memory when a trailer opens. If the operating system kills the app because it runs out of RAM, increase swap to 512 MB:

```bash
sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=512/' /etc/dphys-swapfile
sudo systemctl restart dphys-swapfile
```

Swap can prevent some out-of-memory crashes, but it is much slower than RAM and causes additional microSD-card writes.

# Troubleshooting

## GitHub returns `404` during installation

The repository is private. Anonymous release downloads work only after `tnichol00/PiStick` is public. The installer intentionally does not ask for or store a GitHub token.

## `No published PiStick release exists yet`

Create a GitHub Release. A branch, commit, or tag without a published Release is intentionally ignored.

## The release predates the installer

Publish a new release from a commit that contains `pistick-release.json` and the external data path support in `main.py`. This safety check prevents an older release from writing private data inside a replaceable release folder.

## `pistick.service` repeatedly restarts

Read the error:

```bash
journalctl -u pistick.service -b -n 100 --no-pager
```

Then rerun the release updater. It will reinstall only if a newer published release exists:

```bash
sudo pistick-update
```

## The TMDB setup screen remains visible

Validate the private configuration file:

```bash
sudo python3 -m json.tool /etc/pistick/config.json
```

Then restart PiStick:

```bash
sudo systemctl restart pistick.service
```

## Controller is paired but not detected

```bash
ls -l /dev/input/js* /dev/input/by-id/*joystick* 2>/dev/null
```

If no device appears, reconnect the controller. If it appears, reboot once so all group changes are active.

## Trailer playback is slow

The trailer screen uses QtWebEngine/Chromium and is the heaviest part of PiStick. It is created only when **Watch Trailer** is selected and destroyed after the trailer closes. The original Pi Zero W may still struggle with YouTube playback even when the rest of the interface is responsive.

# Creating a release

PiStick releases should be created from tested `main` commits:

1. Merge and test the intended changes on `main`.
2. Open the repository's **Releases** page.
3. Select **Draft a new release**.
4. Create a version tag such as `v0.1.0-alpha` or `v1.0.0` targeting the tested `main` commit.
5. Add release notes.
6. Publish the release. It may be marked as a pre-release, but it cannot remain a draft.

GitHub's automatic **Source code (tar.gz)** archive contains `main.py`, `install.sh`, `config.example.json`, and the release manifest. The installer downloads that archive directly from the published release record, so the manual updater advances with the app through the same release system.

# Data and privacy

- TMDB metadata and posters come from TMDB.
- Trailers play through YouTube.
- Profiles and watch history are stored locally on the Pi.
- The TMDB token is stored locally in `/etc/pistick/config.json` with restricted permissions.
- The installer does not collect or store a GitHub credential.
- `config.json` and PiStick state files must never be committed.

# Development status

PiStick is under active development. Movie/show selection currently records local watch state and calls the placeholder `watch_title()` function. Actual movie and episode playback will be connected through Jellyfin later.

This product uses the TMDB API but is not endorsed or certified by TMDB.
