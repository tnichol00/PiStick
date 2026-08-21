#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

# PiStick appliance installer for the original Raspberry Pi Zero W.
# This branch intentionally supports only its ARMv6 CPU and Cog/WPE kiosk.

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
LOCAL_BIN="$(root_path /usr/local/bin)"
LOCAL_LIBEXEC="$(root_path /usr/local/libexec)"
SUDOERS_PATH="$(root_path /etc/sudoers.d/pistick-system)"
CONFIG_PATH="${DATA_ROOT}/data/config.json"
OWNER_MARKER="${DATA_ROOT}/.pistick-owner"

TEMP_DIR=""
SOURCE_DIR="${PISTICK_SOURCE_DIR:-}"
TMDB_TOKEN="${PISTICK_TMDB_TOKEN:-}"
MACHINE="${PISTICK_MACHINE:-$(uname -m)}"
ACTIVE_RELEASE_DIR=""
ACTIVE_RELEASE_ID=""
PREVIOUS_RELEASE_DIR=""
RELEASE_SWITCHED=0
RELEASE_COMMITTED=0

cleanup() {
    local status=$?
    if [[ "$status" -ne 0 && "$RELEASE_SWITCHED" == "1" && "$RELEASE_COMMITTED" != "1" ]]; then
        restore_previous_release || true
    fi
    if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
        rm -rf -- "$TEMP_DIR"
    fi
    TMDB_TOKEN=""
    return "$status"
}
trap cleanup EXIT

fail() {
    printf '\nPiStick installation failed: %s\n' "$*" >&2
    exit 1
}

step() {
    printf '\n[PiStick] %s\n' "$*"
}

release_path_is_safe() {
    [[ -n "$1" && "$1" == "${INSTALL_ROOT}/releases/"* ]]
}

restore_previous_release() {
    [[ -n "$PREVIOUS_RELEASE_DIR" && -d "$PREVIOUS_RELEASE_DIR" ]] || return 0
    release_path_is_safe "$PREVIOUS_RELEASE_DIR" || return 1

    step "Restoring the last working PiStick release"
    rm -f -- "${INSTALL_ROOT}/current.rollback"
    ln -s "$PREVIOUS_RELEASE_DIR" "${INSTALL_ROOT}/current.rollback"
    mv -Tf "${INSTALL_ROOT}/current.rollback" "${INSTALL_ROOT}/current"
    RELEASE_SWITCHED=0

    if [[ "$TEST_MODE" != "1" ]]; then
        systemctl daemon-reload >/dev/null 2>&1 || true
        systemctl restart pistick-server.service >/dev/null 2>&1 || true
        systemctl restart pistick-kiosk.service >/dev/null 2>&1 || true
    fi
}

package_available() {
    apt-cache show "$1" >/dev/null 2>&1
}

package_installed() {
    dpkg-query -W -f='${Status}' "$1" 2>/dev/null \
        | grep -Fq 'install ok installed'
}

require_root() {
    if [[ "$TEST_MODE" == "1" ]]; then
        return
    fi
    [[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "Run this installer with sudo."
}

acquire_install_lock() {
    if [[ "$TEST_MODE" == "1" ]]; then
        return
    fi
    install -d -m 0755 /run/lock
    exec 9>/run/lock/pistick-install.lock
    flock -n 9 || fail "Another PiStick installer or update is already running."
}

check_platform() {
    if [[ "$TEST_MODE" == "1" ]]; then
        return
    fi
    [[ -r /etc/os-release ]] || fail "Raspberry Pi OS could not be identified."
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "${ID:-}" == "raspbian" ]] \
        || fail "Use Raspberry Pi OS Legacy Lite (32-bit, Bookworm)."
    [[ "$MACHINE" == "armv6l" ]] \
        || fail "This branch is optimized only for the original ARMv6 Raspberry Pi Zero W, not $MACHINE."
    local debian_major
    debian_major="${VERSION_ID%%.*}"
    [[ "$debian_major" == "12" ]] \
        || fail "The original Pi Zero W needs Raspberry Pi OS Legacy Lite (32-bit, Bookworm/Debian 12)."
}

resolve_target_user() {
    if [[ "$TEST_MODE" == "1" ]]; then
        TARGET_USER="${PISTICK_USER:-pistick}"
        TARGET_GROUP="${PISTICK_GROUP:-pistick}"
        TARGET_HOME="${PISTICK_HOME:-/home/pistick}"
        TARGET_UID="${PISTICK_UID:-1000}"
        TARGET_GID="${PISTICK_GID:-1000}"
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
    TARGET_GID="$(id -g "$TARGET_USER")"
    [[ "$TARGET_UID" -ge 1000 ]] || fail "PiStick must run as a regular user, not root."
    TARGET_GROUP="$(id -gn "$TARGET_USER")"
    TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
    [[ -d "$TARGET_HOME" ]] || fail "Home directory for '$TARGET_USER' does not exist."
}

install_packages() {
    if [[ "$TEST_MODE" == "1" ]]; then
        return
    fi

    local packages=(
        ca-certificates
        curl
        git
        python3
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
        dbus-daemon
        cog
        libgl1-mesa-dri
        gstreamer1.0-alsa
        gstreamer1.0-libav
        gstreamer1.0-plugins-base
        gstreamer1.0-plugins-good
        gstreamer1.0-plugins-bad
        pi-bluetooth
    )

    local package all_installed=1
    for package in "${packages[@]}"; do
        if ! package_installed "$package"; then
            all_installed=0
            break
        fi
    done
    if [[ "$all_installed" == "1" ]]; then
        step "All Pi Zero W runtime packages are already installed"
        return
    fi

    step "Installing the Pi Zero W browser, media, Python, network, and controller packages"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    package_available cog \
        || fail "Raspberry Pi OS Legacy Bookworm did not provide the ARMv6-compatible Cog browser."
    apt-get install -y --no-install-recommends "${packages[@]}"
}

prepare_private_data() {
    step "Preparing PiStick's private data directories"
    install -d -m 0750 \
        "$DATA_ROOT" \
        "${DATA_ROOT}/data" \
        "$CACHE_ROOT" \
        "${CACHE_ROOT}/cog"

    if [[ "$TEST_MODE" != "1" ]]; then
        local expected_owner existing_owner
        expected_owner="${TARGET_UID}:${TARGET_GID}"
        existing_owner="$(head -n 1 "$OWNER_MARKER" 2>/dev/null || true)"
        if [[ "$existing_owner" != "$expected_owner" ]]; then
            # Correct legacy ownership once. Future updates avoid recursively
            # walking TMDB and browser caches that already belong to this user.
            chown -R "$TARGET_USER:$TARGET_GROUP" "$DATA_ROOT" "$CACHE_ROOT"
            printf '%s\n' "$expected_owner" >"$OWNER_MARKER"
            chown "$TARGET_USER:$TARGET_GROUP" "$OWNER_MARKER"
            chmod 0644 "$OWNER_MARKER"
        else
            chown "$TARGET_USER:$TARGET_GROUP" \
                "$DATA_ROOT" "${DATA_ROOT}/data" \
                "$CACHE_ROOT" "${CACHE_ROOT}/cog"
        fi
    fi
}

prepare_source() {
    if [[ -n "$SOURCE_DIR" ]]; then
        SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
        return
    fi

    TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pistick-pi.XXXXXX")"
    SOURCE_DIR="${TEMP_DIR}/source"
    step "Downloading only the Pi Zero W application files"
    git clone --depth 1 --filter=blob:none --no-checkout --branch "$SOURCE_BRANCH" \
        "https://github.com/${REPOSITORY}.git" "$SOURCE_DIR"
    git -C "$SOURCE_DIR" sparse-checkout init --no-cone
    git -C "$SOURCE_DIR" sparse-checkout set --no-cone \
        /server.py \
        /playback_api.py \
        /PI_ZERO_W_README.md \
        /LICENSE \
        /pistick_server/ \
        /pi/
    git -C "$SOURCE_DIR" checkout --quiet
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
        pi/kiosk-cog.sh
        pi/diagnose.sh
        pi/pistick-system-helper.py
        pi/configure-tmdb.sh
        PI_ZERO_W_README.md
    )
    local path
    for path in "${required[@]}"; do
        [[ -f "${SOURCE_DIR}/${path}" ]] || fail "The branch is missing $path."
    done
    bash -n "${SOURCE_DIR}/pi/launch-kiosk.sh"
    bash -n "${SOURCE_DIR}/pi/kiosk-cog.sh"
    bash -n "${SOURCE_DIR}/pi/diagnose.sh"
    bash -n "${SOURCE_DIR}/pi/configure-tmdb.sh"
    python3 -B - \
        "${SOURCE_DIR}/server.py" \
        "${SOURCE_DIR}/playback_api.py" \
        "${SOURCE_DIR}/pistick_server/app.py" \
        "${SOURCE_DIR}/pistick_server/config.py" \
        "${SOURCE_DIR}/pistick_server/state.py" \
        "${SOURCE_DIR}/pistick_server/tmdb.py" \
        "${SOURCE_DIR}/pistick_server/system_control.py" \
        "${SOURCE_DIR}/pi/pistick-system-helper.py" <<'PY'
import ast
from pathlib import Path
import sys

for filename in sys.argv[1:]:
    ast.parse(Path(filename).read_bytes(), filename=filename)
PY
}

install_release() {
    step "Installing the PiStick application"
    local revision release_base release_id staging release_dir current_link counter previous
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

    PREVIOUS_RELEASE_DIR=""
    if [[ -L "$current_link" ]]; then
        previous="$(readlink -f -- "$current_link" 2>/dev/null || true)"
        if release_path_is_safe "$previous" && [[ -d "$previous" ]]; then
            PREVIOUS_RELEASE_DIR="$previous"
        fi
    fi

    install -d -m 0755 "${INSTALL_ROOT}/releases"
    install -d -m 0755 "$staging"
    install -m 0644 "${SOURCE_DIR}/server.py" "$staging/server.py"
    install -m 0644 "${SOURCE_DIR}/playback_api.py" "$staging/playback_api.py"
    cp -a "${SOURCE_DIR}/pistick_server" "$staging/pistick_server"
    cp -a "${SOURCE_DIR}/pi" "$staging/pi"
    install -m 0644 "${SOURCE_DIR}/PI_ZERO_W_README.md" "$staging/PI_ZERO_W_README.md"
    [[ ! -f "${SOURCE_DIR}/LICENSE" ]] || install -m 0644 "${SOURCE_DIR}/LICENSE" "$staging/LICENSE"
    find "$staging" -type d -name __pycache__ -prune -exec rm -rf -- {} +
    # Compile once during installation instead of making the 1 GHz ARMv6 core
    # parse every module at each boot. -OO also omits runtime docstrings.
    python3 -OO -m compileall -q "$staging" \
        || fail "The optimized PiStick bytecode could not be prepared."

    chmod -R u=rwX,go=rX "$staging"
    chmod 0755 \
        "$staging/pi/launch-kiosk.sh" \
        "$staging/pi/kiosk-cog.sh" \
        "$staging/pi/diagnose.sh"

    mv "$staging" "$release_dir"
    rm -f -- "${current_link}.new"
    ln -s "$release_dir" "${current_link}.new"
    mv -Tf "${current_link}.new" "$current_link"
    ACTIVE_RELEASE_DIR="$release_dir"
    ACTIVE_RELEASE_ID="$release_id"
    RELEASE_SWITCHED=1
}

verify_installed_release() {
    step "Verifying the installed release as the PiStick service user"
    local current_target
    current_target="$(readlink -f -- "${INSTALL_ROOT}/current" 2>/dev/null || true)"
    [[ "$current_target" == "$ACTIVE_RELEASE_DIR" ]] \
        || fail "The active release link was not switched correctly."

    if [[ "$TEST_MODE" == "1" ]]; then
        [[ -x "${ACTIVE_RELEASE_DIR}/pi/launch-kiosk.sh" ]] \
            || fail "The installed kiosk launcher is not executable."
        [[ -r "${ACTIVE_RELEASE_DIR}/pistick_server/static/index.html" ]] \
            || fail "The installed web interface is not readable."
        if ! python3 -I -OO -B -c \
            'import sys; sys.path.insert(0, sys.argv[1]); from pistick_server.app import PiStickApplication' \
            "$ACTIVE_RELEASE_DIR"; then
            fail "The installed PiStick server cannot import its application module."
        fi
    else
        if ! runuser -u "$TARGET_USER" -- /usr/bin/test \
            -x "${ACTIVE_RELEASE_DIR}/pi/launch-kiosk.sh"; then
            fail "The PiStick service user cannot execute the installed kiosk launcher."
        fi
        if ! runuser -u "$TARGET_USER" -- /usr/bin/test \
            -r "${ACTIVE_RELEASE_DIR}/pistick_server/static/index.html"; then
            fail "The PiStick service user cannot read the installed web interface."
        fi
        if ! runuser -u "$TARGET_USER" -- /usr/bin/python3 -I -OO -B -c \
            'import sys; sys.path.insert(0, sys.argv[1]); from pistick_server.app import PiStickApplication' \
            "$ACTIVE_RELEASE_DIR"; then
            fail "The PiStick service user cannot read or import the installed application."
        fi
    fi
}

cleanup_old_releases() {
    step "Removing all previous PiStick application versions"
    local releases_root current_target candidate removed stale_link inactive_cache
    local runtime_marker legacy_logs legacy_xwrapper legacy_xwrapper_contents
    releases_root="$(readlink -f -- "${INSTALL_ROOT}/releases" 2>/dev/null || true)"
    current_target="$(readlink -f -- "${INSTALL_ROOT}/current" 2>/dev/null || true)"
    [[ -n "$releases_root" && -d "$releases_root" ]] \
        || fail "The PiStick release directory could not be verified; no old versions were deleted."
    [[ -n "$current_target" && -d "$current_target" ]] \
        || fail "The active PiStick release could not be verified; no old versions were deleted."
    case "$current_target" in
        "$releases_root"/*) ;;
        *) fail "The active release is outside the managed release directory; no old versions were deleted." ;;
    esac

    removed=0
    while IFS= read -r -d '' candidate; do
        [[ "$candidate" == "$current_target" ]] && continue
        case "$candidate" in
            "$releases_root"/*) ;;
            *) fail "Refusing to delete an unexpected release path: $candidate" ;;
        esac
        rm -rf -- "$candidate"
        removed=$((removed + 1))
    done < <(find "$releases_root" -mindepth 1 -maxdepth 1 -print0)

    [[ -d "$current_target" ]] || fail "Old-version cleanup removed the active release unexpectedly."
    for stale_link in "${INSTALL_ROOT}/current.new" "${INSTALL_ROOT}/current.rollback"; do
        if [[ -L "$stale_link" || -f "$stale_link" ]]; then
            rm -f -- "$stale_link"
        fi
    done

    # Older revisions created a Chromium profile even on ARMv6, where only
    # Cog/WPE can run. It contains no configuration used by this branch.
    inactive_cache="${CACHE_ROOT}/chromium"
    if [[ -e "$inactive_cache" || -L "$inactive_cache" ]]; then
        [[ "$inactive_cache" == "${CACHE_ROOT}/chromium" ]] \
            || fail "Refusing to remove an unexpected browser-cache path."
        rm -rf -- "$inactive_cache"
    fi

    # Remove exact runtime artifacts written by superseded PiStick builds. The
    # configuration, profile/watch state, TMDB cache, and current Cog data live
    # elsewhere and are deliberately preserved.
    runtime_marker="${DATA_ROOT}/data/runtime.json"
    if [[ -f "$runtime_marker" || -L "$runtime_marker" ]]; then
        rm -f -- "$runtime_marker"
    fi
    legacy_logs="${DATA_ROOT}/logs"
    if [[ -d "$legacy_logs" ]]; then
        find "$legacy_logs" -mindepth 1 -maxdepth 1 \
            \( -type f -o -type l \) \
            \( -name 'server.log' -o -name 'server.log.[0-9]*' \) -delete
        rmdir "$legacy_logs" 2>/dev/null || true
    fi

    # Old multi-Pi installers created this Xorg policy even on an ARMv6 Pi.
    # Delete it only when it is byte-for-byte the PiStick-generated policy so
    # a user-customized or package-owned Xwrapper configuration is untouched.
    legacy_xwrapper="$(root_path /etc/X11/Xwrapper.config)"
    if [[ -f "$legacy_xwrapper" ]]; then
        legacy_xwrapper_contents="$(<"$legacy_xwrapper")"
        if [[ "$legacy_xwrapper_contents" == $'allowed_users=anybody\nneeds_root_rights=yes' ]]; then
            rm -f -- "$legacy_xwrapper"
        fi
    fi
    printf '[PiStick] Removed %d inactive application version(s) and obsolete runtime files; configuration and watch data were preserved.\n' "$removed"
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
lan_enabled = data.get("lan_enabled", True)
if isinstance(lan_enabled, str):
    lan_enabled = lan_enabled.strip().lower() in {"1", "true", "yes", "on"}
data["lan_enabled"] = bool(lan_enabled)
serialized = json.dumps(data, indent=2) + "\n"
try:
    with open(path, encoding="utf-8") as source:
        unchanged = source.read() == serialized
except OSError:
    unchanged = False
if not unchanged:
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as destination:
        destination.write(serialized)
    os.replace(temporary, path)
' "$CONFIG_PATH" <<<"$TMDB_TOKEN"
    chmod 0600 "$CONFIG_PATH"

    if [[ "$TEST_MODE" != "1" ]]; then
        chown "$TARGET_USER:$TARGET_GROUP" "$CONFIG_PATH"
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

install_system_helpers() {
    step "Installing HDMI-only network and controller controls"
    local sudoers_temporary="${SUDOERS_PATH}.tmp"
    install -d -m 0755 "$LOCAL_LIBEXEC" "$LOCAL_BIN" "$(dirname "$SUDOERS_PATH")"
    install -m 0755 \
        "${INSTALL_ROOT}/current/pi/pistick-system-helper.py" \
        "${LOCAL_LIBEXEC}/pistick-system-helper"
    install -m 0755 \
        "${INSTALL_ROOT}/current/pi/configure-tmdb.sh" \
        "${LOCAL_BIN}/pistick-configure-tmdb"
    install -m 0755 \
        "${INSTALL_ROOT}/current/pi/diagnose.sh" \
        "${LOCAL_BIN}/pistick-diagnose"
    cat >"$sudoers_temporary" <<EOF
${TARGET_USER} ALL=(root) NOPASSWD: /usr/local/libexec/pistick-system-helper status
${TARGET_USER} ALL=(root) NOPASSWD: /usr/local/libexec/pistick-system-helper wifi-scan
${TARGET_USER} ALL=(root) NOPASSWD: /usr/local/libexec/pistick-system-helper wifi-connect
${TARGET_USER} ALL=(root) NOPASSWD: /usr/local/libexec/pistick-system-helper bluetooth-scan
${TARGET_USER} ALL=(root) NOPASSWD: /usr/local/libexec/pistick-system-helper bluetooth-pair
EOF
    chmod 0440 "$sudoers_temporary"
    if [[ "$TEST_MODE" != "1" ]]; then
        visudo -cf "$sudoers_temporary" >/dev/null \
            || fail "The PiStick system-control permission file is invalid."
    fi
    mv -f "$sudoers_temporary" "$SUDOERS_PATH"
    if [[ "$TEST_MODE" != "1" ]]; then
        if [[ "$(hostname 2>/dev/null || true)" != "pistick" ]]; then
            hostnamectl set-hostname pistick
        fi
        python3 - /etc/hosts <<'PY'
from pathlib import Path
import os
import sys

path = Path(sys.argv[1])
original = path.read_text(encoding="utf-8")
lines = original.splitlines()
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
content = "\n".join(updated) + "\n"
if original != content:
    temporary.write_text(content, encoding="utf-8")
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
After=local-fs.target

[Service]
Type=simple
User=${TARGET_USER}
Group=${TARGET_GROUP}
UMask=0077
WorkingDirectory=/opt/pistick/current
Environment=PISTICK_ALLOW_LAN_BIND=1
Environment=PISTICK_ALLOW_HTTP_PORT=1
Environment=PISTICK_DEFAULT_LAN=1
Environment=PISTICK_LOW_MEMORY=1
Environment=PISTICK_SYSTEM_HELPER=/usr/local/libexec/pistick-system-helper
Environment=MALLOC_ARENA_MAX=2
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=/usr/bin/python3 -OO -B /opt/pistick/current/server.py --host 0.0.0.0 --port 80 --data-dir /var/lib/pistick/data
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
After=pistick-server.service systemd-user-sessions.service
Conflicts=getty@tty1.service

[Service]
Type=simple
User=${TARGET_USER}
Group=${TARGET_GROUP}
PAMName=login
WorkingDirectory=/opt/pistick/current
Environment=HOME=${TARGET_HOME}
Environment=PISTICK_URL=http://127.0.0.1/?platform=pi-zero-w&release=${ACTIVE_RELEASE_ID}
Environment=PISTICK_COG_PROFILE=/var/cache/pistick/cog
Environment=PISTICK_MAX_DISPLAY_MODE=1280x720@60
Environment=MALLOC_ARENA_MAX=2
RuntimeDirectory=pistick-kiosk
RuntimeDirectoryMode=0700
Environment=XDG_RUNTIME_DIR=/run/pistick-kiosk
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=journal+console
StandardError=journal+console
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
    cat >"${LOCAL_BIN}/update-pistick" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

temporary="\$(mktemp "\${TMPDIR:-/tmp}/update-pistick.XXXXXX.sh")"
trap 'rm -f -- "\$temporary"' EXIT

printf '[PiStick] Downloading the newest Pi Zero W installer...\n'
cache_buster="\$(date +%s)"
curl -fsSL -H 'Cache-Control: no-cache' \
    "https://raw.githubusercontent.com/${REPOSITORY}/refs/heads/${SOURCE_BRANCH}/install.sh?cache=\${cache_buster}" \
    -o "\$temporary"
if ! grep -Fq 'SOURCE_BRANCH="${SOURCE_BRANCH}"' "\$temporary"; then
    printf 'PiStick update failed: the downloaded installer was not the Pi Zero W installer.\n' >&2
    exit 1
fi

if [[ \${EUID:-\$(id -u)} -eq 0 ]]; then
    bash "\$temporary"
else
    sudo bash "\$temporary"
fi
EOF
    chmod 0755 "${LOCAL_BIN}/update-pistick"

    ln -sfn "update-pistick" "${LOCAL_BIN}/pistick-update"
}

start_services() {
    if [[ "$TEST_MODE" == "1" ]]; then
        return
    fi
    step "Starting PiStick"
    systemctl daemon-reload \
        || fail "systemd could not reload the PiStick service definitions."
    systemctl enable bluetooth.service >/dev/null 2>&1 || true
    systemctl restart bluetooth.service >/dev/null 2>&1 || true
    systemctl enable avahi-daemon.service >/dev/null 2>&1 || true
    systemctl restart avahi-daemon.service >/dev/null 2>&1 || true
    systemctl disable --now getty@tty1.service >/dev/null 2>&1 || true
    systemctl enable pistick-server.service pistick-kiosk.service >/dev/null \
        || fail "systemd could not enable the PiStick boot services."
    systemctl restart pistick-server.service \
        || fail "The PiStick server service could not be started. Run: journalctl -u pistick-server -n 100"

    local attempt health expected_release homepage asset
    for ((attempt = 0; attempt < 30; attempt += 1)); do
        if curl -fsS --max-time 2 http://127.0.0.1/health >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    if ! health="$(curl -fsS --max-time 3 http://127.0.0.1/health)"; then
        fail "The local PiStick server did not pass its health check. Run: journalctl -u pistick-server -n 100"
    fi
    expected_release="\"release\":\"${ACTIVE_RELEASE_ID}\""
    if [[ "$health" != *"$expected_release"* ]]; then
        fail "The local server is still running an older PiStick release."
    fi

    # Validate the HTML and both static assets before committing a release.
    # Keeping curl output out of a grep pipeline avoids the old error-23 false
    # failure caused by grep closing the pipe early under pipefail.
    if ! homepage="$(curl -fsS --max-time 5 \
        "http://127.0.0.1/?platform=pi-zero-w&release=${ACTIVE_RELEASE_ID}")"; then
        fail "The local PiStick server started, but its web interface could not be downloaded."
    fi
    if [[ "$homepage" != *'/styles.css?v='* || "$homepage" != *'/app.js?v='* ]]; then
        fail "The local PiStick server started, but its web interface did not pass validation."
    fi
    for asset in styles.css app.js; do
        curl -fsS --max-time 5 "http://127.0.0.1/${asset}?install=${ACTIVE_RELEASE_ID}" >/dev/null \
            || fail "The local PiStick server started, but ${asset} could not be downloaded."
    done

    # The application release is considered healthy once its import, server,
    # HTML, CSS and JavaScript checks pass. Cog/WPE 2.38 on the original ARMv6
    # Zero W can occasionally TRAP/SEGV while the DRM/WebProcess is settling.
    # That browser-runtime restart must not roll a healthy application release
    # back to an older version. The kiosk wrapper now restarts Cog internally
    # and systemd continues supervising the wrapper.
    systemctl restart pistick-kiosk.service \
        || fail "The PiStick kiosk service could not be started. Run: sudo pistick-diagnose"

    sleep 2
    systemctl is-active --quiet pistick-kiosk.service \
        || fail "The PiStick kiosk wrapper did not stay active. Run: sudo pistick-diagnose"
}

print_summary() {
    printf '\nPiStick for Pi Zero W is installed.\n'
    printf '  Application: /opt/pistick/current\n'
    printf '  Release: %s\n' "$ACTIVE_RELEASE_ID"
    printf '  Private data: /var/lib/pistick/data\n'
    printf '  Guide: /opt/pistick/current/PI_ZERO_W_README.md\n'
    if [[ "$TEST_MODE" != "1" ]]; then
        printf '\nReboot once so the new controller/input groups apply:\n  sudo reboot\n'
        printf '\nPair a Bluetooth controller over SSH with:\n  bluetoothctl\n'
        printf '\nOther devices on this Wi-Fi can open:\n  http://pistick.local\n'
        printf '\nChange the TMDB token over SSH with:\n  sudo pistick-configure-tmdb\n'
        printf '\nInstall future PiStick updates with:\n  sudo update-pistick\n'
        printf '\nCollect safe display diagnostics with:\n  sudo pistick-diagnose\n'
    fi
}

main() {
    require_root
    acquire_install_lock
    check_platform
    resolve_target_user
    install_packages
    prepare_private_data
    prepare_source
    check_source
    collect_token
    install_release
    verify_installed_release
    write_config
    configure_user_access
    install_system_helpers
    write_services
    write_update_command
    start_services
    RELEASE_COMMITTED=1
    cleanup_old_releases
    print_summary
}

main "$@"
