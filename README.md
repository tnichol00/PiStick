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

## Recommended branch

The most optimized Raspberry Pi version is on the `efficiency-overhaul` branch. The installation commands below install that branch.

## Hardware requirements

- Raspberry Pi Zero W or newer Raspberry Pi
- 8 GB or larger microSD card
- Stable 5 V power supply
- Mini-HDMI cable for final TV use
- 2.4 GHz Wi-Fi for the original Pi Zero W
- Optional Bluetooth controller or USB controller with a micro-USB OTG adapter

The original Pi Zero W has a single-core ARMv6 processor and 512 MB of RAM. PiStick is optimized for it, but YouTube's Chromium-based trailer player remains demanding. A Pi Zero 2 W or newer model will provide a noticeably smoother experience.

# Dedicated Raspberry Pi installation

This setup uses Raspberry Pi OS Lite and launches PiStick directly in a minimal X11 session. It does not install or display a normal desktop, taskbar, file manager, or desktop icons.

The final boot flow is:

```text
Power on -> Raspberry Pi OS Lite -> minimal display server -> PiStick fullscreen
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

## 3. Update Raspberry Pi OS

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

Reconnect through SSH after the reboot.

## 4. Install PiStick's system dependencies

```bash
sudo apt install -y \
  git \
  python3 \
  python3-requests \
  python3-pygame \
  python3-pyqt5 \
  python3-pyqt5.qtwebengine \
  xserver-xorg-core \
  xserver-xorg-input-all \
  xserver-xorg-legacy \
  xinit \
  matchbox-window-manager \
  fonts-dejavu-core
```

Confirm that the important Python modules load:

```bash
python3 -c "import requests, pygame; from PyQt5.QtWebEngineWidgets import QWebEngineView; print('PiStick dependencies are ready')"
```

## 5. Download PiStick

```bash
cd /home/pistick
git clone --branch efficiency-overhaul --single-branch https://github.com/tnichol00/PiStick.git
cd PiStick
```

If the repository is private, GitHub will ask for authentication. Enter your GitHub username and use a GitHub personal access token with read access to this repository instead of your GitHub password. Never put the token inside `config.json` or commit it to the repository.

## 6. Add a TMDB token

PiStick needs a free TMDB API Read Access Token.

1. Create or sign into a [TMDB account](https://www.themoviedb.org/signup).
2. Open [TMDB API settings](https://www.themoviedb.org/settings/api) and request API access.
3. Copy the **API Read Access Token**.
4. On the Pi, create the private configuration file:

```bash
nano /home/pistick/PiStick/config.json
```

Paste this, replacing the example value:

```json
{
  "tmdb_read_token": "PASTE_YOUR_TMDB_READ_ACCESS_TOKEN_HERE"
}
```

Save with `Ctrl+O`, press `Enter`, and exit with `Ctrl+X`.

`config.json` and PiStick's profile/watch-state file are excluded from Git, so they will not be uploaded by normal commits.

## 7. Give PiStick access to graphics and controllers

```bash
sudo usermod -aG video,input pistick
getent group render >/dev/null && sudo usermod -aG render pistick
```

Allow the dedicated kiosk service to start X11:

```bash
sudo nano /etc/X11/Xwrapper.config
```

Set the file to:

```ini
allowed_users=anybody
needs_root_rights=yes
```

This setting is intended for a dedicated PiStick appliance. Do not use it on a shared Linux computer.

Reboot so the new group permissions take effect:

```bash
sudo reboot
```

## 8. Test PiStick manually

Reconnect through SSH, then run:

```bash
PISTICK_LOW_MEMORY=1 xinit /usr/bin/sh -c '/usr/bin/matchbox-window-manager -use_titlebar no & exec /usr/bin/python3 /home/pistick/PiStick/main.py' -- :0 vt7 -keeptty -nolisten tcp
```

PiStick should appear fullscreen on the connected display. Press `Ctrl+C` in the SSH terminal to stop this manual test.

## 9. Start PiStick automatically at boot

Create a system service:

```bash
sudo nano /etc/systemd/system/pistick.service
```

Paste:

```ini
[Unit]
Description=PiStick TV interface
Wants=network-online.target
After=network-online.target systemd-user-sessions.service

[Service]
Type=simple
User=pistick
Group=pistick
WorkingDirectory=/home/pistick/PiStick
Environment=HOME=/home/pistick
Environment=DISPLAY=:0
Environment=PISTICK_LOW_MEMORY=1
Environment=QT_QPA_PLATFORM=xcb
ExecStart=/usr/bin/xinit /usr/bin/sh -c '/usr/bin/matchbox-window-manager -use_titlebar no & exec /usr/bin/python3 /home/pistick/PiStick/main.py' -- :0 vt7 -keeptty -nolisten tcp
Restart=always
RestartSec=2
StandardInput=tty
TTYPath=/dev/tty7
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes

[Install]
WantedBy=multi-user.target
```

Save the file, then enable and start PiStick:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pistick.service
```

The Pi will now boot directly into PiStick. If the app crashes, systemd restarts it after two seconds. SSH remains available for maintenance.

## 10. Check status and logs

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

USB controllers can be connected through a powered micro-USB OTG adapter. To pair a Bluetooth controller:

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

# Updating PiStick

```bash
sudo systemctl stop pistick.service
cd /home/pistick/PiStick
git pull --ff-only
python3 -m py_compile main.py
sudo systemctl start pistick.service
```

Your `config.json` and `pistick_state.json` files are preserved during normal updates.

# Optional: view PiStick remotely

SSH is the best option for installing, updating, and reading logs. If PiStick's X11 session is running, you can temporarily view it from another computer with `x11vnc`:

```bash
sudo apt install -y x11vnc
mkdir -p /home/pistick/.vnc
x11vnc -storepasswd /home/pistick/.vnc/passwd
x11vnc -display :0 -auth guess -forever -shared -rfbauth /home/pistick/.vnc/passwd
```

Connect a VNC viewer to `pistick.local:5900`. VNC consumes extra CPU and can make animations look slower than they will over HDMI, so do not use it for final performance measurements.

# Optional: increase swap on a Pi Zero W

QtWebEngine can briefly use a large amount of memory when a trailer opens. If the operating system kills the app because it runs out of RAM, increase swap to 512 MB:

```bash
sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=512/' /etc/dphys-swapfile
sudo systemctl restart dphys-swapfile
```

Swap prevents some out-of-memory crashes but is much slower than RAM and causes additional microSD-card writes. It does not make the app faster.

# Troubleshooting

## `pistick.service` repeatedly restarts

Read the error first:

```bash
journalctl -u pistick.service -b -n 100 --no-pager
```

Also verify the application still compiles:

```bash
cd /home/pistick/PiStick
python3 -m py_compile main.py
```

## The TMDB setup screen remains visible

Check that `/home/pistick/PiStick/config.json` contains valid JSON and the API Read Access Token, not the shorter TMDB API key.

```bash
python3 -m json.tool /home/pistick/PiStick/config.json
```

Then restart PiStick:

```bash
sudo systemctl restart pistick.service
```

## `No module named PyQt5` or missing QtWebEngine

```bash
sudo apt update
sudo apt install --reinstall python3-pyqt5 python3-pyqt5.qtwebengine
```

## Blank screen or X11 permission error

Confirm that `/etc/X11/Xwrapper.config` contains the two lines from step 7, then check the service log. Also confirm that the PiStick user belongs to the required groups:

```bash
groups pistick
```

## Controller is paired but not detected

```bash
ls -l /dev/input/js* /dev/input/by-id/*joystick* 2>/dev/null
```

If no device appears, reconnect the controller. If it appears, confirm `pistick` belongs to the `input` group and reboot.

## Trailer playback is slow

The trailer screen uses QtWebEngine/Chromium and is the heaviest part of PiStick. It is created only when **Watch Trailer** is selected and destroyed after the trailer closes. The original Pi Zero W may still struggle with YouTube playback even when the rest of the interface is responsive.

# Data and privacy

- TMDB metadata and posters come from TMDB.
- Trailers play through YouTube.
- Profiles and watch history are stored locally in `pistick_state.json`.
- The TMDB token is stored locally in `config.json`.
- `config.json` and `pistick_state.json` should never be committed.

# Development status

PiStick is under active development. Movie/show selection currently records local watch state and calls the placeholder `watch_title()` function. Actual movie and episode playback will be connected through Jellyfin later.

This product uses the TMDB API but is not endorsed or certified by TMDB.
