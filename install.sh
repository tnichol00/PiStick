#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

# PiStick appliance installer for the original Raspberry Pi Zero W.
# This branch intentionally uses the lightweight PiStick server and the
# Raspberry Pi OS Chromium package instead of Qt WebEngine.

REPOSITORY="tnichol00/PiStick"
SOURCE_BRANCH="agent/pi-zero-w"
TEST_MODE="${PISTICK_TEST_MODE:-0}"
TEST_ROOT="${PISTICK_TEST_ROOT:-}"

root_path() {
    printf '%s%s' "$TEST_ROOT" "$1"
}

INSTALL_ROOT="$(root_path /opt/pistick)"
DATA_ROOT="$(root_path /var/lib/pistick)"
CACHE_ROOT="$(root_path /var/cache/pistick)"
SYSTEMD_ROOT="$(root_path /etc/systemd/system)"
XWRAPPER_PATH="$(root_path /etc/X11/Xwrapper.config)"
LOCAL_BIN="$(root_path /usr/local/bin)"
LOCAL_LIBEXEC="$(root_path /usr/local/libexec)"
SUDOERS_PATH="$(root_path /etc/sudoers.d/pistick-system)"
CONFIG_PATH="${DATA_ROOT}/data/config.json"

TEMP_DIR=""
SOURCE_DIR="${PISTICK_SOURCE_DIR:-}"
TMDB_TOKEN="${PISTICK_TMDB_TOKEN:-}"

cleanup() {
    if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
        rm -rf -- "$TEMP_DIR"
    fi
    TMDB_TOKEN=""
}
trap cleanup EXIT

fail() {
    printf '\nPiStick installation failed: %s\n' "$*" >&2
    exit 1
}

step() {
    printf '\n[PiStick] %s\n' "$*"
}

package_available() {
    apt-cache show "$1" >/dev/null 2>&1
}

require_root() {
    if [[ "$TEST_MODE" == "1" ]]; then
        return
    fi
    [[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "Run this installer with sudo."
}

check_platform() {
    if [[ "$TEST_MODE" == "1" ]]; then
        return
    fi
    [[ -r /etc/os-release ]] || fail "Raspberry Pi OS could not be identified."
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}" in
        raspbian|debian) ;;
        *) fail "Use Raspberry Pi OS Legacy Lite (32-bit, Bookworm)." ;;
    esac
    local machine
    machine="$(uname -m)"
    case "$machine" in
        armv6l|armv7l|aarch64) ;;
        *) fail "This installer is for Raspberry Pi hardware, not $machine." ;;
    esac
    if [[ "$machine" == "armv6l" ]]; then
        local debian_major
        debian_major="${VERSION_ID%%.*}"
        if [[ "$debian_major" =~ ^[0-9]+$ ]] && ((debian_major >= 13)); then
            fail "The original Pi Zero W needs Raspberry Pi OS Legacy Lite (32-bit, Bookworm). Trixie's browsers require a newer CPU."
        fi
    fi
    if [[ "$machine" == "aarch64" ]]; then
        printf 'Warning: the original Pi Zero W should use Raspberry Pi OS Legacy Lite (32-bit, Bookworm).\n' >&2
    fi
}

resolve_target_user() {
    if [[ "$TEST_MODE" == "1" ]]; then
        TARGET_USER="${PISTICK_USER:-pistick}"
        TARGET_GROUP="${PISTICK_GROUP:-pistick}"
        TARGET_HOME="${PISTICK_HOME:-/home/pistick}"
        TARGET_UID="${PISTICK_UID:-1000}"
        return
    fi

    TARGET_USER="${PISTICK_USER:-}"
    if [[ -z "$TARGET_USER" && -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        TARGET_USER="$SUDO_USER"
    fi
    if [[ -z "$TARGET_USER" ]]; then
        TARGET_USER="$(getent passwd 1000 | cut -d: -f1 || true)"
    fi
    [[ -n "$TARGET_USER" ]] || fail "No regular user was found. Set PISTICK_USER and run again."
    getent passwd "$TARGET_USER" >/dev/null || fail "User '$TARGET_USER' does not exist."
    TARGET_UID="$(id -u "$TARGET_USER")"
    [[ "$TARGET_UID" -ge 1000 ]] || fail "PiStick must run as a regular user, not root."
    TARGET_GROUP="$(id -gn "$TARGET_USER")"
    TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
    [[ -d "$TARGET_HOME" ]] || fail "Home directory for '$TARGET_USER' does not exist."
}

install_packages() {
    if [[ "$TEST_MODE" == "1" ]]; then
        BROWSER_PACKAGE="chromium"
        return
    fi

    step "Installing the minimal display, browser, Python, and Bluetooth packages"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update

    local packages=(
        ca-certificates
        curl
        git
        python3
        xserver-xorg-core
        xserver-xorg-legacy
        xinit
        openbox
        x11-xserver-utils
        dbus-x11
        alsa-utils
        pulseaudio
        pulseaudio-utils
        fonts-dejavu-core
        fonts-liberation
        bluez
        rfkill
        network-manager
        avahi-daemon
        sudo
    )

    if package_available chromium-browser; then
        BROWSER_PACKAGE="chromium-browser"
    elif package_available chromium; then
        BROWSER_PACKAGE="chromium"
    else
        fail "Raspberry Pi OS did not provide Chromium. Update the OS and run the installer again."
    fi
    packages+=("$BROWSER_PACKAGE")

    if package_available unclutter-xfixes; then
        packages+=(unclutter-xfixes)
    elif package_available unclutter; then
        packages+=(unclutter)
    fi
    if package_available pi-bluetooth; then
        packages+=(pi-bluetooth)
    fi

    apt-get install -y --no-install-recommends "${packages[@]}"
}

prepare_source() {
    if [[ -n "$SOURCE_DIR" ]]; then
        SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
        return
    fi

    TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pistick-pi.XXXXXX")"
    SOURCE_DIR="${TEMP_DIR}/source"
    step "Downloading the Pi Zero W branch"
    git clone --depth 1 --branch "$SOURCE_BRANCH" \
        "https://github.com/${REPOSITORY}.git" "$SOURCE_DIR"
}

check_source() {
    local required=(
        server.py
        playback_api.py
        pistick_server/app.py
        pistick_server/config.py
        pistick_server/state.py
        pistick_server/tmdb.py
        pistick_server/system_control.py
        pistick_server/static/index.html
        pistick_server/static/app.js
        pistick_server/static/styles.css
        pi/launch-kiosk.sh
        pi/kiosk-session.sh
        pi/pistick-system-helper.py
        pi/configure-tmdb.sh
        PI_ZERO_W_README.md
    )
    local path
    for path in "${required[@]}"; do
        [[ -f "${SOURCE_DIR}/${path}" ]] || fail "The branch is missing $path."
    done
    bash -n "${SOURCE_DIR}/pi/launch-kiosk.sh"
    bash -n "${SOURCE_DIR}/pi/kiosk-session.sh"
    bash -n "${SOURCE_DIR}/pi/configure-tmdb.sh"
    python3 -m py_compile \
        "${SOURCE_DIR}/server.py" \
        "${SOURCE_DIR}/playback_api.py" \
        "${SOURCE_DIR}/pistick_server/app.py" \
        "${SOURCE_DIR}/pistick_server/config.py" \
        "${SOURCE_DIR}/pistick_server/state.py" \
        "${SOURCE_DIR}/pistick_server/tmdb.py" \
        "${SOURCE_DIR}/pistick_server/system_control.py" \
        "${SOURCE_DIR}/pi/pistick-system-helper.py"
}

install_release() {
    step "Installing the PiStick application"
    local revision release_base release_id staging release_dir current_link counter
    revision="$(git -C "$SOURCE_DIR" rev-parse --short=12 HEAD 2>/dev/null || printf 'local')"
    release_base="$(date -u +%Y%m%d%H%M%S)-${revision}"
    release_id="$release_base"
    counter=1
    while [[ -e "${INSTALL_ROOT}/releases/${release_id}" || -e "${INSTALL_ROOT}/releases/.${release_id}.staging" ]]; do
        release_id="${release_base}-${counter}"
        counter=$((counter + 1))
    done
    release_dir="${INSTALL_ROOT}/releases/${release_id}"
    staging="${INSTALL_ROOT}/releases/.${release_id}.staging"
    current_link="${INSTALL_ROOT}/current"

    install -d -m 0755 "${INSTALL_ROOT}/releases"
    install -d -m 0755 "$staging"
    install -m 0644 "${SOURCE_DIR}/server.py" "$staging/server.py"
    install -m 0644 "${SOURCE_DIR}/playback_api.py" "$staging/playback_api.py"
    cp -a "${SOURCE_DIR}/pistick_server" "$staging/pistick_server"
    cp -a "${SOURCE_DIR}/pi" "$staging/pi"
    install -m 0644 "${SOURCE_DIR}/PI_ZERO_W_README.md" "$staging/PI_ZERO_W_README.md"
    [[ ! -f "${SOURCE_DIR}/LICENSE" ]] || install -m 0644 "${SOURCE_DIR}/LICENSE" "$staging/LICENSE"
    chmod 0755 "$staging/pi/launch-kiosk.sh" "$staging/pi/kiosk-session.sh"
    find "$staging" -type d -name __pycache__ -prune -exec rm -rf -- {} +

    mv "$staging" "$release_dir"
    rm -f -- "${current_link}.new"
    ln -s "$release_dir" "${current_link}.new"
    mv -Tf "${current_link}.new" "$current_link"
}

usable_token() {
    local value="$1"
    [[ ${#value} -ge 24 && "$value" != *PASTE_YOUR* && "$value" != *KEEP_THE_QUOTES* ]]
}

read_existing_token() {
    [[ -f "$CONFIG_PATH" ]] || return 0
    python3 -c '
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8-sig"))
    print(str(value.get("tmdb_read_token") or value.get("tmdb_token") or "").strip())
except (OSError, ValueError, TypeError, AttributeError):
    pass
' "$CONFIG_PATH" 2>/dev/null || true
}

validate_token() {
    local value="$1"
    if [[ "${PISTICK_SKIP_TMDB_VALIDATION:-0}" == "1" ]]; then
        return 0
    fi
    python3 -c '
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

token = sys.stdin.read().strip()
request = Request(
    "https://api.themoviedb.org/3/configuration",
    headers={"Authorization": "Bearer " + token, "Accept": "application/json", "User-Agent": "PiStick-Pi-Installer/2"},
)
try:
    with urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("unexpected response")
except HTTPError as error:
    if error.code in (401, 403):
        print("TMDB rejected that token. Copy the long API Read Access Token.", file=sys.stderr)
    else:
        print("TMDB validation failed with HTTP " + str(error.code) + ".", file=sys.stderr)
    raise SystemExit(1)
except (URLError, TimeoutError, OSError, RuntimeError) as error:
    print("TMDB could not be reached: " + str(error), file=sys.stderr)
    raise SystemExit(1)
' <<<"$value"
}

collect_token() {
    local existing
    existing="$(read_existing_token)"
    if [[ -z "$TMDB_TOKEN" ]] && usable_token "$existing"; then
        TMDB_TOKEN="$existing"
        printf 'Keeping the existing TMDB token.\n'
        return
    fi

    if [[ -n "$TMDB_TOKEN" ]]; then
        usable_token "$TMDB_TOKEN" || fail "PISTICK_TMDB_TOKEN is not a valid-looking Read Access Token."
        validate_token "$TMDB_TOKEN" || fail "The supplied TMDB token could not be validated."
        return
    fi

    [[ -t 0 ]] || fail "A TMDB token is required. Run the installer interactively or set PISTICK_TMDB_TOKEN."
    while true; do
        printf '\nPaste your long TMDB API Read Access Token (typing is hidden): '
        IFS= read -r -s TMDB_TOKEN
        printf '\n'
        if ! usable_token "$TMDB_TOKEN"; then
            printf 'That does not look like the long TMDB Read Access Token. Try again.\n' >&2
            continue
        fi
        if validate_token "$TMDB_TOKEN"; then
            break
        fi
    done
}

write_config() {
    install -d -m 0750 "${DATA_ROOT}/data" "${DATA_ROOT}/logs" "$CACHE_ROOT/chromium"
    python3 -c '
import json, os, secrets, sys
path = sys.argv[1]
token = sys.stdin.read().strip()
try:
    with open(path, encoding="utf-8-sig") as source:
        data = json.load(source)
except (OSError, ValueError, TypeError):
    data = {}
if not isinstance(data, dict):
    data = {}
data["tmdb_read_token"] = token
data.pop("tmdb_token", None)
try:
    port = int(data.get("port", 8787))
except (TypeError, ValueError):
    port = 8787
data["port"] = port if 1024 <= port <= 65535 else 8787
data["shutdown_token"] = str(data.get("shutdown_token") or secrets.token_urlsafe(32))
data["lan_enabled"] = bool(data.get("lan_enabled", True))
temporary = path + ".tmp"
with open(temporary, "w", encoding="utf-8") as destination:
    json.dump(data, destination, indent=2)
    destination.write("\n")
os.replace(temporary, path)
' "$CONFIG_PATH" <<<"$TMDB_TOKEN"
    chmod 0600 "$CONFIG_PATH"

    if [[ "$TEST_MODE" != "1" ]]; then
        chown -R "$TARGET_USER:$TARGET_GROUP" "$DATA_ROOT" "$CACHE_ROOT"
    fi
}

configure_user_access() {
    if [[ "$TEST_MODE" == "1" ]]; then
        return
    fi
    local groups=() group
    for group in input video render audio bluetooth netdev; do
        if getent group "$group" >/dev/null; then
            groups+=("$group")
        fi
    done
    if ((${#groups[@]})); then
        local joined
        joined="$(IFS=,; printf '%s' "${groups[*]}")"
        usermod -aG "$joined" "$TARGET_USER"
    fi
    rfkill unblock bluetooth 2>/dev/null || true
}

write_xwrapper() {
    install -d -m 0755 "$(dirname "$XWRAPPER_PATH")"
    cat >"$XWRAPPER_PATH" <<'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF
    chmod 0644 "$XWRAPPER_PATH"
}

install_system_helpers() {
    step "Installing HDMI-only network and controller controls"
    install -d -m 0755 "$LOCAL_LIBEXEC" "$LOCAL_BIN" "$(dirname "$SUDOERS_PATH")"
    install -m 0755 \
        "${INSTALL_ROOT}/current/pi/pistick-system-helper.py" \
        "${LOCAL_LIBEXEC}/pistick-system-helper"
    install -m 0755 \
        "${INSTALL_ROOT}/current/pi/configure-tmdb.sh" \
        "${LOCAL_BIN}/pistick-configure-tmdb"
    cat >"$SUDOERS_PATH" <<EOF
${TARGET_USER} ALL=(root) NOPASSWD: /usr/local/libexec/pistick-system-helper status
${TARGET_USER} ALL=(root) NOPASSWD: /usr/local/libexec/pistick-system-helper wifi-scan
${TARGET_USER} ALL=(root) NOPASSWD: /usr/local/libexec/pistick-system-helper wifi-connect
${TARGET_USER} ALL=(root) NOPASSWD: /usr/local/libexec/pistick-system-helper bluetooth-scan
${TARGET_USER} ALL=(root) NOPASSWD: /usr/local/libexec/pistick-system-helper bluetooth-pair
EOF
    chmod 0440 "$SUDOERS_PATH"
    if [[ "$TEST_MODE" != "1" ]]; then
        visudo -cf "$SUDOERS_PATH" >/dev/null || fail "The PiStick system-control permission file is invalid."
        hostnamectl set-hostname pistick
        python3 - /etc/hosts <<'PY'
from pathlib import Path
import os
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
replacement = "127.0.1.1\tpistick"
updated = []
replaced = False
for line in lines:
    fields = line.split("#", 1)[0].split()
    if fields and fields[0] == "127.0.1.1":
        if not replaced:
            updated.append(replacement)
            replaced = True
    else:
        updated.append(line)
if not replaced:
    updated.append(replacement)
temporary = path.with_name(".hosts.pistick.tmp")
temporary.write_text("\n".join(updated) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
    fi
}

write_services() {
    step "Creating the boot services"
    install -d -m 0755 "$SYSTEMD_ROOT"

    cat >"${SYSTEMD_ROOT}/pistick-server.service" <<EOF
[Unit]
Description=PiStick local application server
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${TARGET_USER}
Group=${TARGET_GROUP}
WorkingDirectory=/opt/pistick/current
Environment=PYTHONUNBUFFERED=1
Environment=PISTICK_LOW_MEMORY=1
Environment=PISTICK_ALLOW_LAN_BIND=1
Environment=PISTICK_ALLOW_HTTP_PORT=1
Environment=PISTICK_DEFAULT_LAN=1
Environment=PISTICK_SYSTEM_HELPER=/usr/local/libexec/pistick-system-helper
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=/usr/bin/python3 /opt/pistick/current/server.py --host 0.0.0.0 --port 80 --data-dir /var/lib/pistick/data
Restart=on-failure
RestartSec=3
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

    cat >"${SYSTEMD_ROOT}/pistick-kiosk.service" <<EOF
[Unit]
Description=PiStick fullscreen television interface
Requires=pistick-server.service
After=pistick-server.service network-online.target systemd-user-sessions.service
Conflicts=getty@tty1.service

[Service]
Type=simple
User=${TARGET_USER}
Group=${TARGET_GROUP}
PAMName=login
WorkingDirectory=/opt/pistick/current
Environment=HOME=${TARGET_HOME}
Environment=DISPLAY=:0
Environment=XAUTHORITY=${TARGET_HOME}/.Xauthority
Environment=PISTICK_URL=http://127.0.0.1/?platform=pi-zero-w
Environment=PISTICK_BROWSER_PROFILE=/var/cache/pistick/chromium
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=journal
StandardError=journal
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
UtmpIdentifier=tty1
ExecStart=/opt/pistick/current/pi/launch-kiosk.sh
Restart=always
RestartSec=5
TimeoutStopSec=15
KillMode=mixed

[Install]
WantedBy=multi-user.target
EOF
    chmod 0644 "${SYSTEMD_ROOT}/pistick-server.service" "${SYSTEMD_ROOT}/pistick-kiosk.service"
}

write_update_command() {
    install -d -m 0755 "$LOCAL_BIN"
    cat >"${LOCAL_BIN}/pistick-update" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
temporary="\$(mktemp \"\${TMPDIR:-/tmp}/pistick-update.XXXXXX.sh\")"
trap 'rm -f -- "\$temporary"' EXIT
curl -fsSL "https://raw.githubusercontent.com/${REPOSITORY}/refs/heads/${SOURCE_BRANCH}/install.sh" -o "\$temporary"
if [[ \${EUID:-\$(id -u)} -eq 0 ]]; then
    bash "\$temporary"
else
    sudo bash "\$temporary"
fi
EOF
    chmod 0755 "${LOCAL_BIN}/pistick-update"
}

start_services() {
    if [[ "$TEST_MODE" == "1" ]]; then
        return
    fi
    step "Starting PiStick"
    systemctl daemon-reload
    systemctl enable bluetooth.service >/dev/null 2>&1 || true
    systemctl restart bluetooth.service >/dev/null 2>&1 || true
    systemctl enable avahi-daemon.service >/dev/null 2>&1 || true
    systemctl restart avahi-daemon.service >/dev/null 2>&1 || true
    systemctl disable --now getty@tty1.service >/dev/null 2>&1 || true
    systemctl enable pistick-server.service pistick-kiosk.service >/dev/null
    systemctl restart pistick-server.service

    local attempt
    for attempt in $(seq 1 30); do
        if curl -fsS --max-time 2 http://127.0.0.1/health >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    curl -fsS --max-time 3 http://127.0.0.1/health >/dev/null \
        || fail "The local PiStick server did not pass its health check. Run: journalctl -u pistick-server -n 100"
    systemctl restart pistick-kiosk.service
}

print_summary() {
    printf '\nPiStick for Pi Zero W is installed.\n'
    printf '  Application: /opt/pistick/current\n'
    printf '  Private data: /var/lib/pistick/data\n'
    printf '  Guide: /opt/pistick/current/PI_ZERO_W_README.md\n'
    if [[ "$TEST_MODE" != "1" ]]; then
        printf '\nReboot once so the new controller/input groups apply:\n  sudo reboot\n'
        printf '\nPair a Bluetooth controller over SSH with:\n  bluetoothctl\n'
        printf '\nOther devices on this Wi-Fi can open:\n  http://pistick.local\n'
        printf '\nChange the TMDB token over SSH with:\n  sudo pistick-configure-tmdb\n'
    fi
}

main() {
    require_root
    check_platform
    resolve_target_user
    install_packages
    prepare_source
    check_source
    collect_token
    install_release
    write_config
    configure_user_access
    write_xwrapper
    install_system_helpers
    write_services
    write_update_command
    start_services
    print_summary
}

main "$@"
