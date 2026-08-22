# Install PiStick on a Raspberry Pi Zero W

This branch is made specifically for the original single-core Raspberry Pi Zero W.

## Requirements

- Original Raspberry Pi Zero W
- 8 GB or larger microSD card
- Reliable 5 V power supply
- Mini-HDMI cable
- 2.4 GHz Wi-Fi network
- Computer with a microSD card reader
- Free [TMDB account](https://www.themoviedb.org/signup) and its long **API Read Access Token**

## 1. Prepare the microSD card

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Choose **Raspberry Pi OS (Legacy) Lite (32-bit)** and confirm that its description says **Debian Bookworm**. Do not use Trixie or a 64-bit image on the original Zero W.
3. In **OS customisation**, set:
   - Hostname: `pistick`
   - Username: `pistick`
   - A password
   - Your 2.4 GHz Wi-Fi name, password, and country
   - **Enable SSH** with password authentication
4. Write the card, insert it into the Pi, connect mini-HDMI, and then connect power.
5. Wait about five minutes for the first boot.

## 2. Connect over SSH

Open PowerShell on Windows or Terminal on macOS/Linux:

```bash
ssh pistick@pistick.local
```

If that address does not work, find the Pi's IP address in the router and use it instead:

```bash
ssh pistick@192.168.1.123
```

Replace the example IP address with the Pi's actual address.

## 3. Get the TMDB token

1. Sign in to TMDB.
2. Open [TMDB API settings](https://www.themoviedb.org/settings/api).
3. Request API access if TMDB asks you to complete the form.
4. Copy the long **API Read Access Token**, which normally begins with `eyJ`.

Do not use the shorter v3 API key.

## 4. Install PiStick

Run this entire command in the Pi's SSH window:

```bash
curl -fsSL https://raw.githubusercontent.com/tnichol00/PiStick/refs/heads/Pi-Zero-W/install.sh -o /tmp/install-pistick.sh && sudo bash /tmp/install-pistick.sh
```

Paste the TMDB token when prompted. Nothing appears while it is pasted or typed; this is intentional.

The first installation can take 15–40 minutes. Leave the SSH window open. When the installer finishes, reboot:

```bash
sudo reboot
```

PiStick starts automatically and opens fullscreen on the HDMI display.

## Open PiStick on another device

Connect the other device to the same Wi-Fi and open:

```text
http://pistick.local
```

## Update PiStick

Run either command over SSH:

```bash
sudo pistick-update
```

```bash
sudo update-pistick
```

The updater installs the newest `Pi-Zero-W` branch version, preserves the configuration and watch data, validates the new installation, and then removes the previous application files.
