#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACHINE="${PISTICK_MACHINE:-$(uname -m)}"
BACKEND="${PISTICK_KIOSK_BACKEND:-}"

if [[ -z "$BACKEND" ]]; then
    if [[ "$MACHINE" == "armv6l" ]]; then
        BACKEND="cog"
    else
        BACKEND="chromium"
    fi
fi

case "$BACKEND" in
    cog|chromium) ;;
    *)
        printf 'Unknown PiStick kiosk backend: %s\n' "$BACKEND" >&2
        exit 2
        ;;
esac

if [[ "${1:-}" == "--print-backend" ]]; then
    printf '%s\n' "$BACKEND"
    exit 0
fi

if [[ "$BACKEND" == "cog" ]]; then
    printf '[PiStick] Starting the ARMv6-compatible Cog/WPE kiosk.\n'
    exec "$SCRIPT_DIR/kiosk-cog.sh"
fi

# Xorg leaves a stale lock behind only after an unclean power loss. Do not
# remove a lock while a real X server is running.
if [[ -f /tmp/.X0-lock ]]; then
    x_pid="$(tr -d '[:space:]' </tmp/.X0-lock 2>/dev/null || true)"
    if [[ -z "$x_pid" || ! -d "/proc/$x_pid" ]]; then
        rm -f /tmp/.X0-lock /tmp/.X11-unix/X0 || true
    fi
fi

exec /usr/bin/xinit "$SCRIPT_DIR/kiosk-session.sh" -- :0 vt1 -keeptty -nolisten tcp
