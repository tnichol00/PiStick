#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pistick-pi-installer.XXXXXX")"
TEST_ROOT="${TEST_DIR}/root"
SERVER_PID=""

cleanup() {
    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" >/dev/null 2>&1 || true
        wait "$SERVER_PID" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$TEST_DIR"
}
trap cleanup EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_file() {
    [[ -f "$1" ]] || fail "Expected file: $1"
}

assert_contains() {
    grep -Fq -- "$2" "$1" || fail "Expected '$2' in $1"
}

run_installer() {
    env \
        PISTICK_TEST_MODE=1 \
        PISTICK_TEST_ROOT="$TEST_ROOT" \
        PISTICK_SOURCE_DIR="$PROJECT_DIR" \
        PISTICK_USER=pistick \
        PISTICK_GROUP=pistick \
        PISTICK_HOME=/home/pistick \
        PISTICK_UID=1000 \
        PISTICK_MACHINE=armv6l \
        PISTICK_SKIP_TMDB_VALIDATION=1 \
        "$@" \
        bash "$PROJECT_DIR/install.sh"
}

mkdir -p "$TEST_ROOT"
bash -n "$PROJECT_DIR/install.sh"
bash -n "$PROJECT_DIR/pi/launch-kiosk.sh"
bash -n "$PROJECT_DIR/pi/kiosk-cog.sh"
bash -n "$PROJECT_DIR/pi/kiosk-session.sh"
bash -n "$PROJECT_DIR/pi/diagnose.sh"
bash -n "$PROJECT_DIR/pi/configure-tmdb.sh"

# Even if the interactive token step is interrupted, the private data path is
# prepared so the failure is recognizable and the installer can be rerun.
PARTIAL_ROOT="${TEST_DIR}/partial-root"
mkdir -p "$PARTIAL_ROOT"
if env \
    PISTICK_TEST_MODE=1 \
    PISTICK_TEST_ROOT="$PARTIAL_ROOT" \
    PISTICK_SOURCE_DIR="$PROJECT_DIR" \
    PISTICK_USER=pistick \
    PISTICK_GROUP=pistick \
    PISTICK_HOME=/home/pistick \
    PISTICK_UID=1000 \
    PISTICK_MACHINE=armv6l \
    PISTICK_SKIP_TMDB_VALIDATION=1 \
    bash "$PROJECT_DIR/install.sh" </dev/null >"${TEST_DIR}/partial-install.log" 2>&1; then
    fail "A noninteractive install without a TMDB token unexpectedly succeeded"
fi
[[ -d "$PARTIAL_ROOT/var/lib/pistick/data" ]] \
    || fail "An interrupted install did not prepare the private data path"

run_installer PISTICK_TMDB_TOKEN=test-tmdb-read-token-value

CURRENT="$TEST_ROOT/opt/pistick/current"
[[ -L "$CURRENT" ]] || fail "Expected the current release symlink"
assert_file "$CURRENT/server.py"
assert_file "$CURRENT/playback_api.py"
assert_file "$CURRENT/pistick_server/static/app.js"
assert_file "$CURRENT/pistick_server/system_control.py"
assert_file "$CURRENT/pi/launch-kiosk.sh"
assert_file "$CURRENT/pi/kiosk-cog.sh"
assert_file "$CURRENT/pi/kiosk-session.sh"
assert_file "$CURRENT/pi/diagnose.sh"
assert_file "$CURRENT/pi/pistick-system-helper.py"
assert_file "$CURRENT/pi/configure-tmdb.sh"
assert_file "$CURRENT/PI_ZERO_W_README.md"
assert_file "$TEST_ROOT/var/lib/pistick/data/config.json"
assert_file "$TEST_ROOT/etc/systemd/system/pistick-server.service"
assert_file "$TEST_ROOT/etc/systemd/system/pistick-kiosk.service"
assert_file "$TEST_ROOT/etc/X11/Xwrapper.config"
assert_file "$TEST_ROOT/usr/local/bin/update-pistick"
assert_file "$TEST_ROOT/usr/local/bin/pistick-update"
assert_file "$TEST_ROOT/usr/local/bin/pistick-diagnose"
assert_file "$TEST_ROOT/usr/local/bin/pistick-configure-tmdb"
assert_file "$TEST_ROOT/usr/local/libexec/pistick-system-helper"
assert_file "$TEST_ROOT/etc/sudoers.d/pistick-system"
[[ -L "$TEST_ROOT/usr/local/bin/pistick-update" ]] \
    || fail "The old updater spelling should be a compatibility symlink"
[[ "$(readlink "$TEST_ROOT/usr/local/bin/pistick-update")" == "update-pistick" ]] \
    || fail "The old updater spelling does not target update-pistick"
bash -n "$TEST_ROOT/usr/local/bin/update-pistick"
bash -n "$TEST_ROOT/usr/local/bin/pistick-diagnose"
[[ "$(stat -c '%a' "$TEST_ROOT/var/lib/pistick/data/config.json")" == "600" ]] \
    || fail "The TMDB configuration file must be owner-only"

python3 - "$TEST_ROOT/var/lib/pistick/data/config.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    config = json.load(source)
assert config["tmdb_read_token"] == "test-tmdb-read-token-value"
assert config["port"] == 8787
assert len(config["shutdown_token"]) >= 24
assert config["lan_enabled"] is True
PY

assert_contains "$TEST_ROOT/etc/systemd/system/pistick-server.service" "Environment=PISTICK_LOW_MEMORY=1"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-server.service" "Environment=PISTICK_ALLOW_LAN_BIND=1"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-server.service" "Environment=PISTICK_ALLOW_HTTP_PORT=1"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-server.service" "Environment=PISTICK_DEFAULT_LAN=1"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-server.service" "--host 0.0.0.0"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-server.service" "--port 80"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-server.service" "AmbientCapabilities=CAP_NET_BIND_SERVICE"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-server.service" "PISTICK_SYSTEM_HELPER=/usr/local/libexec/pistick-system-helper"
if grep -Fq "NoNewPrivileges=true" "$TEST_ROOT/etc/systemd/system/pistick-server.service"; then
    fail "The server service prevents its allowlisted sudo helper from running"
fi
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-kiosk.service" "?platform=pi-zero-w"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-kiosk.service" "PISTICK_URL=http://127.0.0.1/?platform=pi-zero-w"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-kiosk.service" "PISTICK_COG_PROFILE=/var/cache/pistick/cog"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-kiosk.service" "PISTICK_MAX_DISPLAY_MODE=1280x720@60"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-kiosk.service" "StandardError=journal+console"
assert_contains "$TEST_ROOT/etc/sudoers.d/pistick-system" "pistick-system-helper wifi-connect"
assert_contains "$TEST_ROOT/usr/local/bin/pistick-configure-tmdb" "TMDB token updated"
assert_contains "$TEST_ROOT/usr/local/bin/update-pistick" "agent/pi-zero-w"
assert_contains "$PROJECT_DIR/install.sh" "avahi-daemon"
assert_contains "$PROJECT_DIR/install.sh" "hostnamectl set-hostname pistick"
assert_contains "$PROJECT_DIR/install.sh" "BROWSER_PACKAGE=\"cog\""
assert_contains "$CURRENT/pi/kiosk-cog.sh" "--platform=drm"
assert_contains "$CURRENT/pi/kiosk-cog.sh" "--gamepad=manette"
assert_contains "$CURRENT/pi/kiosk-cog.sh" "--media-playback-requires-user-gesture=false"
assert_contains "$CURRENT/pi/kiosk-session.sh" "--renderer-process-limit=2"
assert_contains "$CURRENT/pistick_server/static/app.js" "openSearchKeyboard"
assert_contains "$PROJECT_DIR/install.sh" "debian_major >= 13"

[[ "$(PISTICK_MACHINE=armv6l "$CURRENT/pi/launch-kiosk.sh" --print-backend)" == "cog" ]] \
    || fail "The original ARMv6 Zero W did not select Cog/WPE"
[[ "$(PISTICK_MACHINE=armv7l "$CURRENT/pi/launch-kiosk.sh" --print-backend)" == "chromium" ]] \
    || fail "A newer Pi did not keep the Chromium kiosk"

python3 - "$CURRENT/pistick_server/static/styles.css" <<'PY'
from pathlib import Path
import re
import sys

css = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(
    r"html\.pi-zero-w \*,\s*html\.pi-zero-w \*::before,\s*"
    r"html\.pi-zero-w \*::after \{(?P<body>.*?)\}",
    css,
    re.DOTALL,
)
assert match, "Pi Zero W low-memory CSS block is missing"
assert "animation-duration" not in match.group("body"), "low-memory CSS accelerates the spinner"
assert "animation-iteration-count: 1" in css, "reduced-motion animations can spin indefinitely"
PY

token_status="$(
    PISTICK_TEST_MODE=1 \
    PISTICK_TEST_ROOT="$TEST_ROOT" \
    bash "$TEST_ROOT/usr/local/bin/pistick-configure-tmdb" --check
)"
[[ "$token_status" == "TMDB token is set. The token itself was not displayed." ]] \
    || fail "The SSH-only token check did not report a configured token"

replacement_token="replacement-tmdb-read-token-value"
configure_output="$(
    PISTICK_TEST_MODE=1 \
    PISTICK_TEST_ROOT="$TEST_ROOT" \
    PISTICK_SKIP_TMDB_VALIDATION=1 \
    PISTICK_TMDB_TOKEN="$replacement_token" \
    bash "$TEST_ROOT/usr/local/bin/pistick-configure-tmdb"
)"
[[ "$configure_output" != *"$replacement_token"* ]] \
    || fail "The TMDB configuration command printed the private token"
python3 - "$TEST_ROOT/var/lib/pistick/data/config.json" "$replacement_token" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    config = json.load(source)
assert config["tmdb_read_token"] == sys.argv[2]
assert config["port"] == 8787
assert config["lan_enabled"] is True
assert len(config["shutdown_token"]) >= 24
PY
[[ "$(stat -c '%a' "$TEST_ROOT/var/lib/pistick/data/config.json")" == "600" ]] \
    || fail "Changing the TMDB token loosened its file permissions"

MISSING_ROOT="${TEST_DIR}/missing-root"
missing_output="${TEST_DIR}/missing-config.log"
if PISTICK_TEST_MODE=1 PISTICK_TEST_ROOT="$MISSING_ROOT" \
    bash "$TEST_ROOT/usr/local/bin/pistick-configure-tmdb" --check \
    >"$missing_output" 2>&1; then
    fail "The token check unexpectedly accepted a missing installation"
fi
assert_contains "$missing_output" "Run the Pi Zero W installer first"

if grep -Rq "test-tmdb-read-token-value" \
    "$TEST_ROOT/etc/systemd/system" "$TEST_ROOT/opt/pistick"; then
    fail "The TMDB token leaked into application or service files"
fi

# Confirm the installed copy starts as a real loopback HTTP server.
PORT=18787
PISTICK_ALLOW_LAN_BIND=1 \
PISTICK_DEFAULT_LAN=1 \
PISTICK_SYSTEM_HELPER="$TEST_ROOT/usr/local/libexec/pistick-system-helper" \
python3 "$CURRENT/server.py" \
    --host 0.0.0.0 \
    --port "$PORT" \
    --data-dir "$TEST_ROOT/var/lib/pistick/data" \
    >"$TEST_DIR/server.log" 2>&1 &
SERVER_PID=$!
for _attempt in $(seq 1 30); do
    if curl -fsS --max-time 1 "http://127.0.0.1:${PORT}/health" >"$TEST_DIR/health.json" 2>/dev/null; then
        break
    fi
    sleep 0.1
done
python3 - "$TEST_DIR/health.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    health = json.load(source)
assert health["ok"] is True
PY
kill "$SERVER_PID"
wait "$SERVER_PID"
SERVER_PID=""

# A reinstall must preserve watch state and the existing secret.
printf '{"profiles":[],"marker":"keep-me"}\n' >"$TEST_ROOT/var/lib/pistick/data/state.json"
state_hash="$(sha256sum "$TEST_ROOT/var/lib/pistick/data/state.json" | cut -d' ' -f1)"
config_hash="$(sha256sum "$TEST_ROOT/var/lib/pistick/data/config.json" | cut -d' ' -f1)"
run_installer
[[ "$(sha256sum "$TEST_ROOT/var/lib/pistick/data/state.json" | cut -d' ' -f1)" == "$state_hash" ]] \
    || fail "A reinstall changed watch state"
[[ "$(sha256sum "$TEST_ROOT/var/lib/pistick/data/config.json" | cut -d' ' -f1)" == "$config_hash" ]] \
    || fail "A reinstall changed the saved configuration"

# Exercise the exact documented updater command without contacting GitHub.
# The fake curl supplies this checkout's installer, and the fake sudo lets the
# test cover the non-root wrapper path used by GitHub's runner.
FAKE_BIN="${TEST_DIR}/fake-bin"
mkdir -p "$FAKE_BIN"
cat >"$FAKE_BIN/curl" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
output=""
while (($#)); do
    case "$1" in
        -o)
            output="$2"
            shift 2
            ;;
        *) shift ;;
    esac
done
[[ -n "$output" ]]
cp "$PISTICK_TEST_INSTALLER_SOURCE" "$output"
SH
cat >"$FAKE_BIN/sudo" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
exec "$@"
SH
chmod 0755 "$FAKE_BIN/curl" "$FAKE_BIN/sudo"

env \
    PATH="$FAKE_BIN:$PATH" \
    PISTICK_TEST_INSTALLER_SOURCE="$PROJECT_DIR/install.sh" \
    PISTICK_TEST_MODE=1 \
    PISTICK_TEST_ROOT="$TEST_ROOT" \
    PISTICK_SOURCE_DIR="$PROJECT_DIR" \
    PISTICK_USER=pistick \
    PISTICK_GROUP=pistick \
    PISTICK_HOME=/home/pistick \
    PISTICK_UID=1000 \
    PISTICK_MACHINE=armv6l \
    PISTICK_SKIP_TMDB_VALIDATION=1 \
    bash "$TEST_ROOT/usr/local/bin/update-pistick"
[[ "$(sha256sum "$TEST_ROOT/var/lib/pistick/data/state.json" | cut -d' ' -f1)" == "$state_hash" ]] \
    || fail "update-pistick changed watch state"
[[ "$(sha256sum "$TEST_ROOT/var/lib/pistick/data/config.json" | cut -d' ' -f1)" == "$config_hash" ]] \
    || fail "update-pistick changed the saved configuration"

printf 'All Pi Zero W installer checks passed.\n'
