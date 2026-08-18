#!/usr/bin/env bash
set -Eeuo pipefail

export DISPLAY="${DISPLAY:-:0}"
export XDG_CURRENT_DESKTOP=PiStick
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]] && command -v dbus-run-session >/dev/null 2>&1; then
    exec dbus-run-session -- "$0"
fi

PISTICK_URL="${PISTICK_URL:-http://127.0.0.1:8787/?platform=pi-zero-w}"
PROFILE_DIR="${PISTICK_BROWSER_PROFILE:-/var/cache/pistick/chromium}"

xset -dpms >/dev/null 2>&1 || true
xset s off >/dev/null 2>&1 || true
xset s noblank >/dev/null 2>&1 || true

if command -v pulseaudio >/dev/null 2>&1; then
    pulseaudio --start --exit-idle-time=-1 >/dev/null 2>&1 || true
fi

openbox --sm-disable >/dev/null 2>&1 &

if command -v unclutter >/dev/null 2>&1; then
    if unclutter --help 2>&1 | grep -q -- '--timeout'; then
        unclutter --timeout 1 --hide-on-touch >/dev/null 2>&1 &
    else
        unclutter -idle 1 -root >/dev/null 2>&1 &
    fi
fi

for _attempt in $(seq 1 45); do
    if curl -fsS --max-time 2 http://127.0.0.1:8787/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl -fsS --max-time 3 http://127.0.0.1:8787/health >/dev/null \
    || { printf 'PiStick server did not become ready.\n' >&2; exit 1; }

if command -v chromium-browser >/dev/null 2>&1; then
    BROWSER=chromium-browser
elif command -v chromium >/dev/null 2>&1; then
    BROWSER=chromium
else
    printf 'Chromium is not installed.\n' >&2
    exit 1
fi

mkdir -p "$PROFILE_DIR"

# These flags reduce background work without disabling Chromium's sandbox,
# hardware video path, JavaScript, media codecs, or the Gamepad API.
exec "$BROWSER" \
    --kiosk \
    --no-first-run \
    --no-default-browser-check \
    --noerrdialogs \
    --disable-session-crashed-bubble \
    --disable-background-networking \
    --disable-component-update \
    --disable-domain-reliability \
    --disable-extensions \
    --disable-features=MediaRouter,Translate,OptimizationHints \
    --disable-pinch \
    --disable-sync \
    --ozone-platform=x11 \
    --autoplay-policy=no-user-gesture-required \
    --overscroll-history-navigation=0 \
    --renderer-process-limit=2 \
    --disk-cache-size=33554432 \
    --media-cache-size=67108864 \
    --password-store=basic \
    --user-data-dir="$PROFILE_DIR" \
    "$PISTICK_URL"
