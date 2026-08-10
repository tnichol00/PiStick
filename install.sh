#!/usr/bin/env bash
# PiStick first-time installer and release-only updater.
#
# This script is intentionally manual: it installs no timer, cron job, or
# background updater. Run it over SSH for the first install, then run
# `sudo pistick-update` over SSH whenever you want to check for a published
# release.

set -Eeuo pipefail
IFS=$'\n\t'
umask 027

readonly INSTALLER_VERSION="1.0.0"
readonly REPOSITORY="tnichol00/PiStick"
readonly DEFAULT_RELEASES_API="https://api.github.com/repos/${REPOSITORY}/releases?per_page=100"
readonly SERVICE_NAME="pistick.service"

TEST_MODE="${PISTICK_TEST_MODE:-0}"
TEST_ROOT="${PISTICK_TEST_ROOT:-}"
RELEASES_API="${PISTICK_RELEASES_API_URL:-$DEFAULT_RELEASES_API}"
WORK_DIR=""
SWITCH_IN_PROGRESS=0
PREVIOUS_TARGET=""

if [[ -n "$TEST_ROOT" && "$TEST_MODE" != "1" ]]; then
    printf 'PISTICK_TEST_ROOT is available only with PISTICK_TEST_MODE=1.\n' >&2
    exit 2
fi
if [[ -n "$TEST_ROOT" ]]; then
    TEST_ROOT="${TEST_ROOT%/}"
    [[ "$TEST_ROOT" == /* && "$TEST_ROOT" != "/" ]] || {
        printf 'PISTICK_TEST_ROOT must be an absolute, non-root path.\n' >&2
        exit 2
    }
fi

root_path() {
    printf '%s%s' "$TEST_ROOT" "$1"
}

APP_ROOT="$(root_path /opt/pistick)"
RELEASES_DIR="${APP_ROOT}/releases"
CURRENT_LINK="${APP_ROOT}/current"
CONFIG_DIR="$(root_path /etc/pistick)"
CONFIG_FILE="${CONFIG_DIR}/config.json"
INSTALL_CONFIG="${CONFIG_DIR}/install.conf"
DATA_DIR="$(root_path /var/lib/pistick)"
STATE_FILE="${DATA_DIR}/user-data.json"
RELEASE_METADATA="${DATA_DIR}/installed-release.json"
PREVIOUS_METADATA="${DATA_DIR}/previous-release.json"
CACHE_DIR="$(root_path /var/cache/pistick)"
SYSTEMD_DIR="$(root_path /etc/systemd/system)"
X11_DIR="$(root_path /etc/X11)"
LOCAL_SBIN="$(root_path /usr/local/sbin)"
LOCAL_BIN="$(root_path /usr/local/bin)"
LOCAL_LIBEXEC="$(root_path /usr/local/libexec)"
LOCK_FILE="$(root_path /run/lock/pistick-installer.lock)"

log() {
    printf '\n[PiStick] %s\n' "$*"
}

warn() {
    printf '\n[PiStick warning] %s\n' "$*" >&2
}

die() {
    printf '\n[PiStick error] %s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        rm -rf -- "$WORK_DIR"
    fi
}

is_release_target() {
    local candidate="${1:-}"
    [[ -n "$candidate" && "$candidate" == "${RELEASES_DIR}/"* && "$candidate" != "$RELEASES_DIR" ]]
}

rollback_switch() {
    (( SWITCH_IN_PROGRESS == 1 )) || return 0
    SWITCH_IN_PROGRESS=0
    warn "The new release did not pass its startup check. Rolling back."

    if is_release_target "$PREVIOUS_TARGET" && [[ -d "$PREVIOUS_TARGET" ]]; then
        ln -sfn "$PREVIOUS_TARGET" "${APP_ROOT}/.current.rollback"
        mv -Tf "${APP_ROOT}/.current.rollback" "$CURRENT_LINK"
        if [[ -f "$PREVIOUS_METADATA" ]]; then
            install -m 0644 "$PREVIOUS_METADATA" "$RELEASE_METADATA"
        fi
        if [[ "$TEST_MODE" != "1" ]]; then
            systemctl restart "$SERVICE_NAME" >/dev/null 2>&1 || true
        fi
        warn "PiStick was restored to $(basename "$PREVIOUS_TARGET")."
    else
        rm -f -- "$CURRENT_LINK"
        if [[ "$TEST_MODE" != "1" ]]; then
            systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
        fi
        warn "No earlier release was available. PiStick remains stopped."
    fi
}

on_exit() {
    local status=$?
    trap - EXIT
    if (( status != 0 )); then
        rollback_switch || true
    fi
    cleanup
    exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

require_root() {
    if [[ "$TEST_MODE" != "1" && ${EUID:-$(id -u)} -ne 0 ]]; then
        die "Run this installer with sudo: sudo bash install.sh"
    fi
}

acquire_lock() {
    install -d -m 0755 "$(dirname "$LOCK_FILE")"
    exec 9>"$LOCK_FILE"
    flock -n 9 || die "Another PiStick install or update is already running."
}

assert_supported_system() {
    [[ "$TEST_MODE" == "1" ]] && return 0
    [[ -r /etc/os-release ]] || die "This installer requires Raspberry Pi OS or Debian Linux."
    command -v apt-get >/dev/null || die "This installer requires Raspberry Pi OS with apt."

    case "$(uname -m)" in
        armv6l|armv7l|aarch64) ;;
        *) die "This installer is intended for a Raspberry Pi, not $(uname -m)." ;;
    esac
}

valid_username() {
    [[ "$1" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]]
}

resolve_service_user() {
    local candidate=""

    if [[ "$TEST_MODE" == "1" ]]; then
        candidate="$(id -un)"
    elif [[ -f "$INSTALL_CONFIG" ]]; then
        candidate="$(sed -n 's/^PISTICK_USER=//p' "$INSTALL_CONFIG" | head -n 1)"
    elif [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        candidate="$SUDO_USER"
    else
        candidate="$(getent passwd | awk -F: '$3 >= 1000 && $3 < 65534 && $7 !~ /(nologin|false)$/ { print $1; exit }')"
    fi

    valid_username "$candidate" || die "Could not safely determine the SSH user that should run PiStick."
    getent passwd "$candidate" >/dev/null || die "The PiStick user '$candidate' does not exist."

    PISTICK_USER="$candidate"
    PISTICK_GROUP="$(id -gn "$candidate")"
    if [[ "$TEST_MODE" == "1" ]]; then
        USER_HOME="${TEST_ROOT}/home/${candidate}"
        install -d -o "$candidate" -g "$PISTICK_GROUP" -m 0750 "$USER_HOME"
    else
        USER_HOME="$(getent passwd "$candidate" | cut -d: -f6)"
    fi
    [[ -n "$USER_HOME" && "$USER_HOME" == /* ]] || die "Could not determine the home directory for '$candidate'."
}

install_system_packages() {
    [[ "$TEST_MODE" == "1" ]] && return 0
    log "Installing the minimal PiStick system packages"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tar \
        python3 \
        python3-requests \
        python3-pygame \
        python3-pyqt5 \
        python3-pyqt5.qtwebengine \
        xserver-xorg-core \
        xserver-xorg-input-all \
        xserver-xorg-legacy \
        xinit \
        xauth \
        matchbox-window-manager \
        fonts-dejavu-core \
        dbus-x11 \
        libgl1-mesa-dri \
        bluez

    PYGAME_HIDE_SUPPORT_PROMPT=1 python3 -c \
        "import requests, pygame; from PyQt5.QtWebEngineWidgets import QWebEngineView" \
        || die "PiStick's Python/Qt dependencies did not load correctly."

    systemctl enable --now bluetooth.service >/dev/null 2>&1 || true
}

prepare_directories() {
    install -d -o root -g root -m 0755 "$APP_ROOT" "$RELEASES_DIR"
    install -d -o root -g "$PISTICK_GROUP" -m 0750 "$CONFIG_DIR"
    install -d -o "$PISTICK_USER" -g "$PISTICK_GROUP" -m 0750 "$DATA_DIR" "$CACHE_DIR"
    install -d -o root -g root -m 0755 "$SYSTEMD_DIR" "$X11_DIR" \
        "$LOCAL_SBIN" "$LOCAL_BIN" "$LOCAL_LIBEXEC"
    printf 'PISTICK_USER=%s\n' "$PISTICK_USER" >"${INSTALL_CONFIG}.tmp"
    chown root:"$PISTICK_GROUP" "${INSTALL_CONFIG}.tmp"
    chmod 0640 "${INSTALL_CONFIG}.tmp"
    mv -f "${INSTALL_CONFIG}.tmp" "$INSTALL_CONFIG"
}

install_updater_command() {
    local source_path
    source_path="$(readlink -f "${BASH_SOURCE[0]}")"
    [[ -f "$source_path" && -s "$source_path" ]] || \
        die "The installer must be downloaded to a file before it is run; do not pipe it directly into bash."
    if [[ ! -e "${LOCAL_SBIN}/pistick-installer" || \
          ! "$source_path" -ef "${LOCAL_SBIN}/pistick-installer" ]]; then
        install -o root -g root -m 0755 "$source_path" "${LOCAL_SBIN}/pistick-installer"
    fi
    cat >"${LOCAL_BIN}/pistick-update" <<EOF
#!/bin/sh
set -eu
if [ -f "${CURRENT_LINK}/install.sh" ]; then
    exec /usr/bin/bash "${CURRENT_LINK}/install.sh" "\$@"
fi
exec /usr/bin/bash "${LOCAL_SBIN}/pistick-installer" "\$@"
EOF
    chmod 0755 "${LOCAL_BIN}/pistick-update"
}

configure_permissions() {
    [[ "$TEST_MODE" == "1" ]] && return 0
    local group
    for group in video input render; do
        if getent group "$group" >/dev/null; then
            usermod -aG "$group" "$PISTICK_USER"
        fi
    done

    cat >"${X11_DIR}/Xwrapper.config" <<'EOF'
# Managed by the PiStick installer for this dedicated appliance.
allowed_users=anybody
needs_root_rights=yes
EOF
    chmod 0644 "${X11_DIR}/Xwrapper.config"
}

write_runtime_files() {
    cat >"${LOCAL_LIBEXEC}/pistick-session" <<'EOF'
#!/bin/sh
set -eu
/usr/bin/matchbox-window-manager -use_titlebar no &
exec /usr/bin/python3 /opt/pistick/current/main.py
EOF
    chmod 0755 "${LOCAL_LIBEXEC}/pistick-session"

    cat >"${SYSTEMD_DIR}/${SERVICE_NAME}" <<EOF
[Unit]
Description=PiStick television interface
Wants=network-online.target
After=network-online.target systemd-user-sessions.service
Conflicts=getty@tty7.service
StartLimitIntervalSec=60
StartLimitBurst=4

[Service]
Type=simple
User=${PISTICK_USER}
Group=${PISTICK_GROUP}
WorkingDirectory=/opt/pistick/current
Environment="HOME=${USER_HOME}"
Environment="DISPLAY=:0"
Environment="XDG_RUNTIME_DIR=/run/pistick"
Environment="QT_QPA_PLATFORM=xcb"
Environment="PISTICK_LOW_MEMORY=1"
Environment="PISTICK_CONFIG_PATH=/etc/pistick/config.json"
Environment="PISTICK_STATE_PATH=/var/lib/pistick/user-data.json"
Environment="PISTICK_CACHE_DIR=/var/cache/pistick"
RuntimeDirectory=pistick
RuntimeDirectoryMode=0700
UMask=0027
ExecStart=/usr/bin/xinit /usr/local/libexec/pistick-session -- :0 vt7 -keeptty -nolisten tcp
Restart=always
RestartSec=2
KillMode=control-group
TimeoutStopSec=15
StandardInput=tty
TTYPath=/dev/tty7
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes

[Install]
WantedBy=multi-user.target
EOF
    chmod 0644 "${SYSTEMD_DIR}/${SERVICE_NAME}"

    if [[ "$TEST_MODE" != "1" ]]; then
        systemctl daemon-reload
        systemctl enable "$SERVICE_NAME" >/dev/null
    fi
}

config_token() {
    local path="$1"
    [[ -f "$path" ]] || return 1
    python3 - "$path" <<'PY'
import json
import sys

try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    token = str(payload.get("tmdb_read_token") or payload.get("tmdb_token") or "").strip()
except (OSError, ValueError, TypeError, AttributeError):
    raise SystemExit(1)
if not token:
    raise SystemExit(1)
print(token)
PY
}

validate_tmdb_token() {
    local token="$1"
    if [[ "$TEST_MODE" == "1" && "${PISTICK_SKIP_TMDB_VALIDATION:-0}" == "1" ]]; then
        return 0
    fi

    local result
    result="$(python3 -c '
import json
import sys
import urllib.error
import urllib.request

token = sys.stdin.read().strip()
request = urllib.request.Request(
    "https://api.themoviedb.org/3/configuration",
    headers={"Authorization": "Bearer " + token, "User-Agent": "PiStick-Installer/1.0"},
)
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        json.load(response)
except urllib.error.HTTPError as error:
    print("INVALID" if error.code in (401, 403) else "NETWORK")
except (urllib.error.URLError, TimeoutError, ValueError, OSError):
    print("NETWORK")
else:
    print("VALID")
' <<<"$token")"

    case "$result" in
        VALID) return 0 ;;
        INVALID) return 1 ;;
        *) die "TMDB could not be reached to validate the API Read Access Token. Check the Pi's internet connection and run the installer again." ;;
    esac
}

write_config() {
    local token="$1"
    local temporary="${CONFIG_DIR}/.config.json.tmp"
    python3 -c '
import json
import sys

token = sys.stdin.read().strip()
with open(sys.argv[1], "w", encoding="utf-8") as output:
    json.dump({"tmdb_read_token": token}, output, indent=2)
    output.write("\n")
' "$temporary" <<<"$token"
    chown root:"$PISTICK_GROUP" "$temporary"
    chmod 0640 "$temporary"
    mv -f "$temporary" "$CONFIG_FILE"
}

migrate_legacy_data() {
    local legacy_dir="${USER_HOME}/PiStick"

    if [[ ! -f "$CONFIG_FILE" && -f "${legacy_dir}/config.json" ]]; then
        if config_token "${legacy_dir}/config.json" >/dev/null 2>&1; then
            install -o root -g "$PISTICK_GROUP" -m 0640 \
                "${legacy_dir}/config.json" "$CONFIG_FILE"
            log "Preserved the existing TMDB configuration"
        fi
    fi

    if [[ ! -f "$STATE_FILE" && -f "${legacy_dir}/pistick_state.json" ]]; then
        if python3 -m json.tool "${legacy_dir}/pistick_state.json" >/dev/null 2>&1; then
            install -o "$PISTICK_USER" -g "$PISTICK_GROUP" -m 0640 \
                "${legacy_dir}/pistick_state.json" "$STATE_FILE"
            log "Preserved the existing profiles and watch history"
        fi
    fi
}

ensure_tmdb_config() {
    local existing=""
    existing="$(config_token "$CONFIG_FILE" 2>/dev/null || true)"
    if [[ -n "$existing" ]]; then
        return 0
    fi

    local token=""
    while [[ -z "$token" ]]; do
        if [[ "$TEST_MODE" == "1" && -n "${PISTICK_TMDB_TOKEN:-}" ]]; then
            token="$PISTICK_TMDB_TOKEN"
        else
            [[ -r /dev/tty ]] || die "An interactive SSH terminal is required to enter the TMDB token."
            printf '\nPaste your TMDB API Read Access Token: ' >/dev/tty
            IFS= read -r -s token </dev/tty
            printf '\n' >/dev/tty
        fi
        token="${token//$'\r'/}"
        [[ -n "$token" ]] || {
            warn "The token cannot be empty."
            continue
        }
        if ! validate_tmdb_token "$token"; then
            warn "TMDB rejected that token. Paste the API Read Access Token, not the shorter v3 API key."
            token=""
        fi
    done

    write_config "$token"
    unset token existing
    log "TMDB configuration saved privately at /etc/pistick/config.json"
}

fetch_releases_json() {
    local destination="$1"

    if [[ "$TEST_MODE" == "1" && "$RELEASES_API" == file://* ]]; then
        cp -- "${RELEASES_API#file://}" "$destination"
        return 0
    fi

    local status
    status="$(curl -sS -L \
        -H 'Accept: application/vnd.github+json' \
        -H 'X-GitHub-Api-Version: 2022-11-28' \
        -H 'User-Agent: PiStick-Installer/1.0' \
        -o "$destination" -w '%{http_code}' "$RELEASES_API" || true)"

    case "$status" in
        200) ;;
        404) die "GitHub cannot access ${REPOSITORY} without authentication. Make the repository public so a fresh Pi needs only the TMDB token." ;;
        403) die "GitHub refused the release check, usually because its anonymous API rate limit was reached. Try again later." ;;
        *) die "The GitHub release check failed with HTTP ${status:-unknown}." ;;
    esac
}

resolve_latest_release() {
    local releases_json="${WORK_DIR}/releases.json"
    local selected_json="${WORK_DIR}/selected-release.json"
    fetch_releases_json "$releases_json"

    python3 - "$releases_json" "$selected_json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)
if not isinstance(payload, list):
    raise SystemExit("GitHub returned an invalid releases response")

published = [
    release for release in payload
    if isinstance(release, dict)
    and not release.get("draft", False)
    and release.get("published_at")
    and release.get("tag_name")
    and release.get("tarball_url")
    and release.get("id") is not None
]
if not published:
    raise SystemExit("No published PiStick release exists yet")

release = max(published, key=lambda item: (str(item["published_at"]), int(item["id"])))
selected = {
    "id": int(release["id"]),
    "tag_name": str(release["tag_name"]),
    "published_at": str(release["published_at"]),
    "prerelease": bool(release.get("prerelease", False)),
    "tarball_url": str(release["tarball_url"]),
    "html_url": str(release.get("html_url") or ""),
}
with open(sys.argv[2], "w", encoding="utf-8") as destination:
    json.dump(selected, destination, indent=2, sort_keys=True)
    destination.write("\n")
PY

    local fields=()
    mapfile -t fields < <(python3 - "$selected_json" <<'PY'
import json
import sys
release = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ("id", "tag_name", "tarball_url", "published_at", "html_url"):
    print(release[key])
PY
)
    (( ${#fields[@]} == 5 )) || die "Could not read the selected GitHub release."

    RELEASE_ID="${fields[0]}"
    RELEASE_TAG="${fields[1]}"
    RELEASE_TARBALL="${fields[2]}"
    RELEASE_PUBLISHED="${fields[3]}"
    RELEASE_URL="${fields[4]}"
    SELECTED_RELEASE_JSON="$selected_json"

    [[ "$RELEASE_ID" =~ ^[0-9]+$ ]] || die "GitHub returned an invalid release ID."
    [[ -n "$RELEASE_TAG" && "$RELEASE_TAG" != *$'\n'* ]] || die "GitHub returned an invalid release tag."
}

installed_tag() {
    [[ -f "$RELEASE_METADATA" ]] || return 1
    python3 - "$RELEASE_METADATA" <<'PY'
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8"))["tag_name"])
except (OSError, ValueError, KeyError, TypeError):
    raise SystemExit(1)
PY
}

safe_tag_name() {
    local safe
    safe="$(printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_' | cut -c1-80)"
    [[ -n "$safe" ]] || safe="release"
    printf '%s' "$safe"
}

download_release() {
    local archive="${WORK_DIR}/release.tar.gz"
    local staging="${WORK_DIR}/staging"
    log "Downloading published release ${RELEASE_TAG}"

    if [[ "$TEST_MODE" == "1" && "$RELEASE_TARBALL" == file://* ]]; then
        cp -- "${RELEASE_TARBALL#file://}" "$archive"
    else
        curl -fL --retry 3 --connect-timeout 20 \
            -H 'Accept: application/vnd.github+json' \
            -H 'X-GitHub-Api-Version: 2022-11-28' \
            -H 'User-Agent: PiStick-Installer/1.0' \
            -o "$archive" "$RELEASE_TARBALL"
    fi

    local archive_size
    archive_size="$(stat -c '%s' "$archive")"
    (( archive_size > 0 && archive_size <= 104857600 )) || \
        die "The release archive was empty or unexpectedly large."

    mkdir -p "$staging"
    python3 - "$archive" "$staging" <<'PY'
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile

archive = Path(sys.argv[1])
destination = Path(sys.argv[2]).resolve()

with tarfile.open(archive, "r:gz") as bundle:
    members = bundle.getmembers()
    for member in members:
        parts = PurePosixPath(member.name).parts
        if len(parts) < 2:
            continue
        relative_parts = parts[1:]
        if any(part in ("", ".", "..") for part in relative_parts):
            raise SystemExit("Release archive contains an unsafe path")
        target = destination.joinpath(*relative_parts)
        if os.path.commonpath((str(destination), str(target.resolve()))) != str(destination):
            raise SystemExit("Release archive escapes the staging directory")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise SystemExit("Release archive contains an unsupported link or device")
        target.parent.mkdir(parents=True, exist_ok=True)
        source = bundle.extractfile(member)
        if source is None:
            raise SystemExit("Release archive contains an unreadable file")
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
        target.chmod(member.mode & 0o777)
PY

    [[ -f "${staging}/main.py" ]] || die "Release ${RELEASE_TAG} does not contain main.py."
    [[ -f "${staging}/config.example.json" ]] || die "Release ${RELEASE_TAG} does not contain config.example.json."
    [[ -f "${staging}/install.sh" ]] || die "Release ${RELEASE_TAG} does not contain install.sh."
    [[ -f "${staging}/pistick-release.json" ]] || \
        die "Release ${RELEASE_TAG} predates the release installer and cannot safely separate user data."

    python3 - "${staging}/pistick-release.json" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
if (
    manifest.get("installer_schema") != 1
    or manifest.get("entrypoint") != "main.py"
    or manifest.get("updater") != "install.sh"
):
    raise SystemExit("Unsupported PiStick release manifest")
PY
    bash -n "${staging}/install.sh" || die "Release ${RELEASE_TAG} contains an invalid updater."
    grep -q 'PISTICK_CONFIG_PATH' "${staging}/main.py" || die "Release does not support external configuration storage."
    grep -q 'PISTICK_STATE_PATH' "${staging}/main.py" || die "Release does not support external watch-state storage."
    grep -q 'PISTICK_CACHE_DIR' "${staging}/main.py" || die "Release does not support external cache storage."
    python3 - "${staging}/main.py" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text(encoding="utf-8")
compile(source, str(Path(sys.argv[1]).name), "exec")
PY

    STAGING_DIR="$staging"
}

switch_current_release() {
    local release_dir="$1"
    local old_target=""
    if [[ -L "$CURRENT_LINK" ]]; then
        old_target="$(readlink -f "$CURRENT_LINK" || true)"
    fi
    if is_release_target "$old_target" && [[ -d "$old_target" ]]; then
        PREVIOUS_TARGET="$old_target"
        if [[ -f "$RELEASE_METADATA" ]]; then
            install -m 0644 "$RELEASE_METADATA" "$PREVIOUS_METADATA"
        fi
    else
        PREVIOUS_TARGET=""
        rm -f -- "$PREVIOUS_METADATA"
    fi

    ln -sfn "$release_dir" "${APP_ROOT}/.current.new"
    mv -Tf "${APP_ROOT}/.current.new" "$CURRENT_LINK"
    SWITCH_IN_PROGRESS=1
}

service_health_check() {
    if [[ "$TEST_MODE" == "1" ]]; then
        [[ "${PISTICK_TEST_HEALTH_FAIL:-0}" != "1" ]]
        return
    fi

    systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
    local restarts_before restarts_after
    restarts_before="$(systemctl show "$SERVICE_NAME" -p NRestarts --value 2>/dev/null || printf '0')"
    [[ "$restarts_before" =~ ^[0-9]+$ ]] || restarts_before=0

    systemctl start "$SERVICE_NAME" || return 1
    sleep 15
    systemctl is-active --quiet "$SERVICE_NAME" || return 1

    restarts_after="$(systemctl show "$SERVICE_NAME" -p NRestarts --value 2>/dev/null || printf '0')"
    [[ "$restarts_after" =~ ^[0-9]+$ ]] || restarts_after=0
    (( restarts_after == restarts_before ))
}

activate_release() {
    local safe_tag release_dir
    safe_tag="$(safe_tag_name "$RELEASE_TAG")"
    release_dir="${RELEASES_DIR}/${RELEASE_ID}-${safe_tag}"
    is_release_target "$release_dir" || die "Refusing an unsafe release directory."

    if [[ "$TEST_MODE" != "1" ]]; then
        systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi

    if [[ -e "$release_dir" ]]; then
        rm -rf -- "$release_dir"
    fi
    mv "$STAGING_DIR" "$release_dir"
    chown -R root:root "$release_dir"
    chmod -R u=rwX,go=rX "$release_dir"

    switch_current_release "$release_dir"
    if ! service_health_check; then
        die "Release ${RELEASE_TAG} failed its startup health check."
    fi

    install -o "$PISTICK_USER" -g "$PISTICK_GROUP" -m 0644 \
        "$SELECTED_RELEASE_JSON" "$RELEASE_METADATA"
    SWITCH_IN_PROGRESS=0
    log "PiStick ${RELEASE_TAG} is installed and running"
}

start_existing_release() {
    [[ "$TEST_MODE" == "1" ]] && return 0
    systemctl enable "$SERVICE_NAME" >/dev/null
    systemctl restart "$SERVICE_NAME"
}

prune_old_releases() {
    local current_target="" keep_previous=""
    current_target="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
    if is_release_target "$PREVIOUS_TARGET"; then
        keep_previous="$PREVIOUS_TARGET"
    fi

    local candidate
    while IFS= read -r -d '' candidate; do
        if [[ "$candidate" != "$current_target" && "$candidate" != "$keep_previous" ]]; then
            is_release_target "$candidate" || continue
            rm -rf -- "$candidate"
            log "Removed old release snapshot $(basename "$candidate")"
        fi
    done < <(find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -print0)
}

main() {
    require_root
    acquire_lock
    assert_supported_system
    resolve_service_user

    WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pistick-installer.XXXXXX")"
    log "PiStick installer ${INSTALLER_VERSION}"

    install_system_packages
    prepare_directories
    install_updater_command
    configure_permissions
    write_runtime_files
    resolve_latest_release
    migrate_legacy_data
    ensure_tmdb_config

    local current_tag=""
    current_tag="$(installed_tag 2>/dev/null || true)"
    if [[ "$current_tag" == "$RELEASE_TAG" && -L "$CURRENT_LINK" && -f "${CURRENT_LINK}/main.py" ]]; then
        start_existing_release
        log "PiStick is already up to date at ${RELEASE_TAG}"
        printf '\nRun sudo pistick-update over SSH whenever you want to check again.\n'
        return 0
    fi

    download_release
    activate_release
    prune_old_releases

    printf '\nInstallation complete. PiStick will now start automatically at boot.\n'
    printf 'Future updates are manual: SSH into the Pi and run sudo pistick-update.\n'
}

main "$@"
