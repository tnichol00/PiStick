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
        PISTICK_SKIP_TMDB_VALIDATION=1 \
        "$@" \
        bash "$PROJECT_DIR/install.sh"
}

mkdir -p "$TEST_ROOT"
bash -n "$PROJECT_DIR/install.sh"
bash -n "$PROJECT_DIR/pi/launch-kiosk.sh"
bash -n "$PROJECT_DIR/pi/kiosk-session.sh"

run_installer PISTICK_TMDB_TOKEN=test-tmdb-read-token-value

CURRENT="$TEST_ROOT/opt/pistick/current"
[[ -L "$CURRENT" ]] || fail "Expected the current release symlink"
assert_file "$CURRENT/server.py"
assert_file "$CURRENT/playback_api.py"
assert_file "$CURRENT/pistick_server/static/app.js"
assert_file "$CURRENT/pi/launch-kiosk.sh"
assert_file "$CURRENT/pi/kiosk-session.sh"
assert_file "$CURRENT/PI_ZERO_W_README.md"
assert_file "$TEST_ROOT/var/lib/pistick/data/config.json"
assert_file "$TEST_ROOT/etc/systemd/system/pistick-server.service"
assert_file "$TEST_ROOT/etc/systemd/system/pistick-kiosk.service"
assert_file "$TEST_ROOT/etc/X11/Xwrapper.config"
assert_file "$TEST_ROOT/usr/local/bin/pistick-update"

python3 - "$TEST_ROOT/var/lib/pistick/data/config.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    config = json.load(source)
assert config["tmdb_read_token"] == "test-tmdb-read-token-value"
assert config["port"] == 8787
assert len(config["shutdown_token"]) >= 24
PY

assert_contains "$TEST_ROOT/etc/systemd/system/pistick-server.service" "Environment=PISTICK_LOW_MEMORY=1"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-server.service" "--host 127.0.0.1"
assert_contains "$TEST_ROOT/etc/systemd/system/pistick-kiosk.service" "?platform=pi-zero-w"
assert_contains "$CURRENT/pi/kiosk-session.sh" "--renderer-process-limit=2"
assert_contains "$CURRENT/pistick_server/static/app.js" "openSearchKeyboard"
assert_contains "$PROJECT_DIR/install.sh" "debian_major >= 13"

if grep -Rq "test-tmdb-read-token-value" \
    "$TEST_ROOT/etc/systemd/system" "$TEST_ROOT/opt/pistick"; then
    fail "The TMDB token leaked into application or service files"
fi

# Confirm the installed copy starts as a real loopback HTTP server.
PORT=18787
python3 "$CURRENT/server.py" \
    --host 127.0.0.1 \
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

printf 'All Pi Zero W installer checks passed.\n'
