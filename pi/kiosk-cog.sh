#!/usr/bin/env bash
set -Eeuo pipefail

PISTICK_URL="${PISTICK_URL:-http://127.0.0.1/?platform=pi-zero-w}"
PROFILE_DIR="${PISTICK_COG_PROFILE:-/var/cache/pistick/cog}"

if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]] && command -v dbus-run-session >/dev/null 2>&1; then
    exec dbus-run-session -- "$0"
fi

command -v cog >/dev/null 2>&1 \
    || { printf 'Cog is not installed. Run: sudo update-pistick\n' >&2; exit 1; }
[[ -e /dev/dri/card0 ]] \
    || { printf 'The DRM display device /dev/dri/card0 is missing.\n' >&2; exit 1; }

for _attempt in $(seq 1 45); do
    if curl -fsS --max-time 2 http://127.0.0.1/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl -fsS --max-time 3 http://127.0.0.1/health >/dev/null \
    || { printf 'PiStick server did not become ready.\n' >&2; exit 1; }

mkdir -p "$PROFILE_DIR/cache" "$PROFILE_DIR/config" "$PROFILE_DIR/data"
export XDG_CACHE_HOME="$PROFILE_DIR/cache"
export XDG_CONFIG_HOME="$PROFILE_DIR/config"
export XDG_DATA_HOME="$PROFILE_DIR/data"
export GST_REGISTRY="$PROFILE_DIR/gstreamer-registry.bin"
export COG_PLATFORM_DRM_MODE_MAX="${PISTICK_MAX_DISPLAY_MODE:-1280x720@60}"

setterm --blank 0 --powersave off --powerdown 0 >/dev/null 2>&1 || true
if command -v pulseaudio >/dev/null 2>&1; then
    pulseaudio --start --exit-idle-time=-1 >/dev/null 2>&1 || true
fi

cog_args=(
    --platform=drm
    --webprocess-failure=restart
    --bg-color=#09090bff
)
cog_help="$(cog --help-all 2>&1 || true)"
if grep -Fq -- '--gamepad' <<<"$cog_help"; then
    cog_args+=(--gamepad=manette)
fi
if grep -Fq -- '--media-playback-requires-user-gesture' <<<"$cog_help"; then
    cog_args+=(--media-playback-requires-user-gesture=false)
fi

printf '[PiStick] Opening %s with Cog/WPE at up to %s.\n' \
    "$PISTICK_URL" "$COG_PLATFORM_DRM_MODE_MAX"
exec cog "${cog_args[@]}" "$PISTICK_URL"
