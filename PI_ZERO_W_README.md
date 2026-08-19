# PiStick on Raspberry Pi Zero W

This guide installs the `agent/pi-zero-w` edition of PiStick as a television appliance. It is written for the **original Raspberry Pi Zero W** with a single-core ARMv6 processor and 512 MB of RAM. It also works on a Pi Zero 2 W.

The finished boot flow is:

```text
Power on → Raspberry Pi OS Lite → PiStick server → fullscreen browser
```

There is no normal desktop, taskbar, or file manager. PiStick opens automatically on HDMI, can be controlled with a Bluetooth or USB game controller, and can optionally serve the same interface to other devices on your Wi-Fi. The installer uses the ARMv6-compatible Cog/WPE browser on an original Zero W and Chromium on a Zero 2 W or newer Pi.

## Before you begin

You need:

- Raspberry Pi Zero W
- 8 GB or larger microSD card
- Reliable 5 V power supply
- Mini-HDMI cable
- 2.4 GHz Wi-Fi network; the original Zero W cannot use 5 GHz Wi-Fi
- Another computer with a microSD card reader
- Free TMDB account and its long **API Read Access Token**
- Optional Bluetooth controller, or a USB controller with a micro-USB OTG adapter

The original Zero W is much slower than a Zero 2 W. PiStick reduces image sizes, expensive visual effects, background work, and display resolution on this branch. Browsing should be usable, but modern YouTube and third-party video pages can still be demanding. A Zero 2 W is the better choice if smooth video playback is the priority.

## Part 1: Prepare Raspberry Pi OS

### 1. Write the microSD card

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer.
2. Insert the microSD card.
3. In Imager, choose your Raspberry Pi model.
4. Open **Raspberry Pi OS (other)** and select **Raspberry Pi OS (Legacy) Lite (32-bit)**. Confirm that its description says **Debian Bookworm**. Do not select the current Trixie image or any 64-bit image for an original Zero W.
5. Open **OS customisation** and set:
   - Hostname: `pistick`
   - Username: `pistick`
   - A password you will remember
   - Your 2.4 GHz Wi-Fi name and password
   - Your Wi-Fi country
   - **Enable SSH** with password authentication
6. Write the card.
7. Put the card in the Pi.
8. Connect mini-HDMI before connecting power.
9. Power on the Pi and wait about five minutes for its first boot.

Raspberry Pi's official [headless setup guide](https://www.raspberrypi.com/documentation/computers/getting-started.html#headless-remote-setup) explains the Imager options in more detail.

The [official OS download page](https://www.raspberrypi.com/software/operating-systems/) lists Legacy 32-bit Bookworm as compatible with the Zero and Zero W. This matters because the browsers supplied with Trixie require CPU features that the original Zero W does not have. The installer stops with an explanation if it detects Trixie on an ARMv6 Zero W.

### 2. Connect over SSH

On Windows, open PowerShell. On macOS or Linux, open Terminal. Run:

```bash
ssh pistick@pistick.local
```

Type `yes` if asked to trust the host, then enter the password chosen in Imager.

If `pistick.local` does not work, find the Pi's IP address in your router and use it instead:

```bash
ssh pistick@192.168.1.123
```

Replace `192.168.1.123` with the actual address.

## Part 2: Get the TMDB token

1. Sign in at [TMDB](https://www.themoviedb.org/).
2. Open [TMDB API settings](https://www.themoviedb.org/settings/api).
3. Request API access if TMDB asks you to complete the form.
4. Copy the long **API Read Access Token**.

Use the long token, which usually begins with `eyJ`. Do not use the shorter v3 API key.

## Part 3: Install PiStick

In the Pi's SSH window, run this one command:

```bash
curl -fsSL https://raw.githubusercontent.com/tnichol00/PiStick/refs/heads/agent/pi-zero-w/install.sh -o /tmp/install-pistick.sh && sudo bash /tmp/install-pistick.sh
```

The installer will:

- Install Python, NetworkManager, mDNS, Bluetooth tools, media codecs, and the correct kiosk browser for the detected Pi CPU.
- Download only the `agent/pi-zero-w` branch.
- Ask for and validate the TMDB Read Access Token.
- Store the token and watch data outside the application files.
- Add the Pi user to the controller, video, audio, and graphics groups.
- Create separate server and fullscreen-kiosk services.
- Make the Pi discoverable at `pistick.local`.
- Start PiStick automatically after every boot.

When it asks for the TMDB token, paste it and press Enter. Nothing appears while you paste or type; this is intentional.

The first install can take 15–40 minutes on an original Zero W because WPE WebKit and the media packages are large. Leave the SSH window open.

When the installer finishes, reboot:

```bash
sudo reboot
```

The SSH connection will close. PiStick should appear on the connected display after the Pi boots. The first browser launch is slower than later launches.

## Part 4: Use the HDMI settings screen

On the **Who's watching?** screen, select **Settings** beside **Manage Profiles**. This button appears only on the browser running directly on the Pi's HDMI screen. It is deliberately hidden from phones, tablets, and computers.

The settings screen can:

- Turn access for other Wi-Fi devices on or off.
- Scan for and connect to a different 2.4 GHz Wi-Fi network.
- Scan for, pair, trust, and connect a Bluetooth controller.
- Show whether Linux detects a wired USB controller.

Network and Bluetooth actions are rejected unless the request comes from the Pi itself. They cannot be triggered from another device on the LAN.

### Open PiStick on another device

LAN access is on after a new installation. Connect a phone, tablet, or computer to the same Wi-Fi, then open:

```text
http://pistick.local
```

There is no username or password, as requested. Devices share the Pi's profiles, watch history, and TMDB-backed library. Anyone connected to the same Wi-Fi who opens that address can use PiStick and change ordinary profile or watch-state data.

To stop other devices from connecting while keeping HDMI PiStick running:

1. Return to **Who's watching?** on the HDMI screen.
2. Open **Settings**.
3. Under **Other devices**, choose **Turn off**.

The Pi's HDMI interface continues using `http://127.0.0.1` regardless of that switch.

### Connect to a different Wi-Fi network

1. Open **Settings** on the HDMI screen.
2. Under **Wi-Fi**, choose **Scan networks**.
3. Select a network.
4. Enter its password with the controller keyboard and select **Connect**.

The original Pi Zero W supports only 2.4 GHz networks. Changing networks disconnects the Pi from its previous Wi-Fi, but the HDMI interface stays open. Reconnect SSH and other devices through `pistick.local` after the Pi joins the new network.

### Change the TMDB token

The TMDB token cannot be entered or changed through any browser, including the HDMI browser. Connect over SSH and run:

```bash
sudo pistick-configure-tmdb
```

Paste the new long API Read Access Token. The command validates it, saves it privately, and restarts only the PiStick server.

To confirm that a token is saved without displaying or exposing it, run:

```bash
sudo pistick-configure-tmdb --check
```

If `/var/lib/pistick/data` does not exist, the Pi appliance installer did not finish. Do not create an empty `config.json` by hand. Run the one-line installer in **Part 3** again; it safely resumes and preserves any data from a previous completed installation.

## Part 5: Pair a controller

### Bluetooth controller

The easiest method is the HDMI settings screen:

1. Put the controller into pairing mode:
   - **Xbox Wireless Controller:** hold the small Pair button until the Xbox light flashes quickly.
   - **DualShock 4:** hold **PS + Share** until the light bar flashes.
   - **DualSense:** hold **PS + Create** until the lights flash.
   - **Nintendo Switch Pro Controller:** hold the Sync button near the USB port.
2. On **Who's watching?**, open **Settings**.
3. Choose **Pair new controller**.
4. Wait about 10 seconds, select the controller, then choose **Pair**.
5. Press any controller button once after it connects.

The browser does not reveal a controller to a website until the controller has produced input.

#### SSH pairing fallback

If a controller does not appear in the HDMI scan, reconnect over SSH:

```bash
ssh pistick@pistick.local
```

Start the Bluetooth tool:

```bash
bluetoothctl
```

At the `[bluetooth]#` prompt, enter these commands one line at a time:

```text
power on
agent on
default-agent
scan on
```

Wait for a line showing the controller name and an address like `AA:BB:CC:DD:EE:FF`. Then enter:

```text
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
connect AA:BB:CC:DD:EE:FF
scan off
info AA:BB:CC:DD:EE:FF
quit
```

Replace `AA:BB:CC:DD:EE:FF` every time with the controller's real address. The `info` output should show both `Paired: yes` and `Connected: yes`.

Restart only the fullscreen interface so the browser sees the controller:

```bash
sudo systemctl restart pistick-kiosk.service
```

Press any controller button once after PiStick appears.

### USB controller

1. Shut down the Pi:

   ```bash
   sudo poweroff
   ```

2. Connect the controller through a **micro-USB OTG adapter** to the Zero W's USB/data port, not its power-only port.
3. Power the Pi back on.
4. Press any controller button when PiStick appears.

The Zero W has limited USB power. Use a powered USB hub if the controller repeatedly disconnects. Open the HDMI **Settings** screen and choose **Refresh** under **Wired controllers** to confirm it is detected.

### Confirm Linux can see the controller

Run:

```bash
ls -l /dev/input/js* /dev/input/event* 2>/dev/null
grep -A 8 -i -E 'gamepad|joystick|xbox|wireless controller|dualsense' /proc/bus/input/devices
```

If a controller appears there but PiStick does not react, reboot once so the new `input` group membership is applied:

```bash
sudo reboot
```

## Controller controls

- D-pad or left stick: move the selection
- A / Cross: select
- B / Circle: go back or close playback
- X / Square during playback: toggle English subtitles
- Left or Right during playback: seek backward or forward 10 seconds
- A / Cross while the search box is selected: open PiStick's on-screen keyboard

Controllers that expose the standard browser gamepad mapping work best. Some generic controllers swap the A/B or X/Y buttons.

## Service commands

Check both parts of PiStick:

```bash
sudo systemctl status pistick-server.service pistick-kiosk.service
```

Restart PiStick:

```bash
sudo systemctl restart pistick-server.service pistick-kiosk.service
```

Stop the fullscreen app while leaving the local server available:

```bash
sudo systemctl stop pistick-kiosk.service
```

Start it again:

```bash
sudo systemctl start pistick-kiosk.service
```

Prevent PiStick from opening at boot:

```bash
sudo systemctl disable --now pistick-kiosk.service
```

Enable automatic launch again:

```bash
sudo systemctl enable --now pistick-kiosk.service
```

## Logs and troubleshooting

Show the server log:

```bash
journalctl -u pistick-server.service -b -n 100 --no-pager
```

Show the display-browser log:

```bash
journalctl -u pistick-kiosk.service -b -n 150 --no-pager
```

Follow both logs live:

```bash
journalctl -u pistick-server.service -u pistick-kiosk.service -f
```

### Black screen

Run the safe, one-command diagnostic report:

```bash
sudo pistick-diagnose
```

It omits the TMDB token and Wi-Fi credentials. To check manually, confirm both services are active:

```bash
sudo systemctl status pistick-server.service pistick-kiosk.service
```

Then confirm the local page responds:

```bash
curl http://127.0.0.1/health
```

Expected output contains:

```json
{"ok":true,"version":"0.2.0"}
```

If health works, inspect the kiosk log. If health fails, inspect the server log.

### Controller will not reconnect

Run:

```bash
bluetoothctl connect AA:BB:CC:DD:EE:FF
```

If it still fails, remove and pair it again:

```bash
bluetoothctl remove AA:BB:CC:DD:EE:FF
```

Then repeat the pairing steps above.

### Wi-Fi disconnects during playback

The original Zero W supports only 2.4 GHz. Move it closer to the router, make sure the Wi-Fi country was set correctly in Imager, and use a reliable power supply.

### Video is slow

The original Zero W has only one CPU core. PiStick uses the lighter Cog/WPE kiosk at a maximum 720p mode there, but some modern player pages remain too heavy. A Pi Zero 2 W or newer model is recommended for smoother playback.

## Update this branch

Run:

```bash
sudo update-pistick
```

The updater downloads the newest commit from `agent/pi-zero-w`, keeps the TMDB token, profiles, history, and browser data, then restarts PiStick. It validates the server and kiosk before deleting every inactive application version. If the new version fails startup validation, the installer restores the last working version instead of deleting it.

The older `sudo pistick-update` spelling remains as a compatibility alias, but `sudo update-pistick` is the main command going forward.

## Data locations

| Purpose | Location |
| --- | --- |
| Current application | `/opt/pistick/current` |
| Active versioned application | `/opt/pistick/releases` |
| TMDB token and watch state | `/var/lib/pistick/data` |
| Browser profiles and caches | `/var/cache/pistick/cog` and `/var/cache/pistick/chromium` |
| Boot services | `/etc/systemd/system/pistick-*.service` |
| Root-owned system helper | `/usr/local/libexec/pistick-system-helper` |

The server listens for local-network connections, but accepts them only while **Other devices** is enabled and only from private/link-local source addresses using `pistick.local` or the Pi's private IP. Cross-origin requests remain blocked. Wi-Fi, Bluetooth, LAN-toggle, and TMDB-token changes are never available to remote browsers.
