#!/usr/bin/env bash
set -u

section() {
    printf '\n--- %s ---\n' "$1"
}

printf 'PiStick diagnostics (private configuration and Wi-Fi credentials are omitted)\n'

section "Hardware and OS"
if [[ -r /proc/device-tree/model ]]; then
    tr -d '\000' </proc/device-tree/model
    printf '\n'
fi
printf 'Architecture: '
uname -m
if [[ -r /etc/os-release ]]; then
    . /etc/os-release
    printf 'OS: %s\n' "${PRETTY_NAME:-unknown}"
fi

section "Selected kiosk"
if [[ -x /opt/pistick/current/pi/launch-kiosk.sh ]]; then
    printf 'Backend: '
    /opt/pistick/current/pi/launch-kiosk.sh --print-backend 2>&1 || true
fi
for browser in cog chromium-browser chromium; do
    if command -v "$browser" >/dev/null 2>&1; then
        printf '%s: %s\n' "$browser" "$(command -v "$browser")"
        "$browser" --version 2>&1 | head -n 1 || true
    fi
done
if [[ -e /dev/dri/card0 ]]; then
    ls -l /dev/dri/card0 /dev/dri/renderD* 2>/dev/null || true
else
    printf '/dev/dri/card0: missing\n'
fi

section "Service state"
systemctl --no-pager --full status \
    pistick-server.service pistick-kiosk.service 2>&1 || true

section "Local server health"
curl -fsS --max-time 5 http://127.0.0.1/health 2>&1 || true
printf '\n'

section "Current-boot PiStick log"
journalctl -b --no-pager -n 160 \
    -u pistick-server.service -u pistick-kiosk.service 2>&1 || true

section "Memory and disk"
free -h 2>&1 || true
df -h / 2>&1 || true
