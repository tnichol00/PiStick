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

section "Installed release"
current_release="$(readlink -f /opt/pistick/current 2>/dev/null || true)"
if [[ -n "$current_release" && -d "$current_release" ]]; then
    printf 'Current: %s\n' "$current_release"
    printf 'Release entries: '
    find /opt/pistick/releases -mindepth 1 -maxdepth 1 -printf . 2>/dev/null \
        | wc -c \
        | tr -d '[:space:]'
    printf '\n'
    stat -Lc '%A %a %U:%G %n' \
        /opt/pistick/current \
        /opt/pistick/current/pistick_server \
        /opt/pistick/current/pistick_server/app.py \
        /opt/pistick/current/pi/launch-kiosk.sh 2>&1 || true

    service_user="$(systemctl show -p User --value pistick-server.service 2>/dev/null || true)"
    if [[ "${EUID:-$(id -u)}" -eq 0 && -n "$service_user" ]] \
        && runuser -u "$service_user" -- /usr/bin/python3 -I -B -c \
            'import sys; sys.path.insert(0, sys.argv[1]); from pistick_server.app import PiStickApplication' \
            "$current_release" >/dev/null 2>&1; then
        printf 'Server import as %s: OK\n' "$service_user"
    else
        printf 'Server import as service user: FAILED or not checked as root\n'
    fi
else
    printf '/opt/pistick/current: missing or invalid\n'
fi

section "Service state"
systemctl --no-pager --full status \
    pistick-server.service pistick-kiosk.service 2>&1 || true

section "Local server health"
curl -fsS --max-time 5 http://127.0.0.1/health 2>&1 || true
printf '\n'

section "Local web interface"
homepage="$(
    curl -fsS --max-time 5 \
        'http://127.0.0.1/?platform=pi-zero-w&diagnose=1' 2>/dev/null || true
)"
if [[ "$homepage" == *'/styles.css?v='* ]]; then
    printf 'Versioned home page: OK\n'
else
    printf 'Versioned home page: FAILED\n'
fi
curl -fsS --max-time 5 -D - -o /dev/null \
    'http://127.0.0.1/styles.css?diagnose=1' 2>&1 \
    | grep -Ei '^(HTTP/|cache-control:|pragma:|expires:)' || true

section "Current-boot PiStick log"
journalctl -b --no-pager -n 160 \
    -u pistick-server.service -u pistick-kiosk.service 2>&1 || true

section "Memory and disk"
free -h 2>&1 || true
df -h / 2>&1 || true
