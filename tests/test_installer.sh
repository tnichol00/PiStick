#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="${PROJECT_DIR}/install.sh"
if [[ -n "${PISTICK_TEST_OUTPUT_DIR:-}" ]]; then
    TEST_DIR="$PISTICK_TEST_OUTPUT_DIR"
    mkdir -p "$TEST_DIR"
    KEEP_TEST_DIR=1
else
    TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pistick-installer-test.XXXXXX")"
    KEEP_TEST_DIR=0
fi
TEST_ROOT="${TEST_DIR}/root"
FIXTURES="${TEST_DIR}/fixtures"
RELEASES_JSON="${TEST_DIR}/releases.json"

cleanup() {
    if [[ "$KEEP_TEST_DIR" == "0" ]]; then
        rm -rf -- "$TEST_DIR"
    fi
}
trap cleanup EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_file() {
    [[ -f "$1" ]] || fail "Expected file: $1"
}

assert_symlink() {
    [[ -L "$1" ]] || fail "Expected symlink: $1"
}

assert_equals() {
    [[ "$1" == "$2" ]] || fail "Expected '$2', got '$1'"
}

make_release() {
    local id="$1"
    local tag="$2"
    local marker="$3"
    local source_dir="${FIXTURES}/source-${id}"
    local archive="${FIXTURES}/release-${id}.tar.gz"
    mkdir -p "${source_dir}/PiStick-${tag}"

    cat >"${source_dir}/PiStick-${tag}/main.py" <<EOF
import os
PISTICK_CONFIG_PATH = os.getenv("PISTICK_CONFIG_PATH", "config.json")
PISTICK_STATE_PATH = os.getenv("PISTICK_STATE_PATH", "pistick_state.json")
PISTICK_CACHE_DIR = os.getenv("PISTICK_CACHE_DIR", ".cache")
MARKER = "${marker}"
EOF
    cat >"${source_dir}/PiStick-${tag}/config.example.json" <<'EOF'
{
  "tmdb_read_token": "PASTE_YOUR_TMDB_API_READ_ACCESS_TOKEN_HERE"
}
EOF
    cp "$INSTALLER" "${source_dir}/PiStick-${tag}/install.sh"
    chmod 0755 "${source_dir}/PiStick-${tag}/install.sh"
    cat >"${source_dir}/PiStick-${tag}/pistick-release.json" <<'EOF'
{
  "installer_schema": 1,
  "entrypoint": "main.py",
  "updater": "install.sh"
}
EOF
    tar -C "$source_dir" -czf "$archive" "PiStick-${tag}"
    printf '%s' "$archive"
}

write_release_index() {
    local newest_id="$1"
    local newest_tag="$2"
    local newest_archive="$3"
    local newest_date="$4"
    local older_id="${5:-}"
    local older_tag="${6:-}"
    local older_archive="${7:-}"
    local older_date="${8:-}"

    python3 - "$RELEASES_JSON" "$newest_id" "$newest_tag" "$newest_archive" "$newest_date" \
        "$older_id" "$older_tag" "$older_archive" "$older_date" <<'PY'
import json
import sys

output, newest_id, newest_tag, newest_archive, newest_date, older_id, older_tag, older_archive, older_date = sys.argv[1:]
releases = [{
    "id": int(newest_id),
    "tag_name": newest_tag,
    "published_at": newest_date,
    "draft": False,
    "prerelease": "alpha" in newest_tag,
    "tarball_url": "file://" + newest_archive,
    "html_url": "https://example.invalid/" + newest_tag,
}]
if older_id:
    releases.append({
        "id": int(older_id),
        "tag_name": older_tag,
        "published_at": older_date,
        "draft": False,
        "prerelease": "alpha" in older_tag,
        "tarball_url": "file://" + older_archive,
        "html_url": "https://example.invalid/" + older_tag,
    })
releases.append({
    "id": 9999,
    "tag_name": "v999-draft",
    "published_at": "2099-01-01T00:00:00Z",
    "draft": True,
    "prerelease": False,
    "tarball_url": "file:///must-not-be-used",
})
with open(output, "w", encoding="utf-8") as destination:
    json.dump(releases, destination)
PY
}

run_installer() {
    env \
        PISTICK_TEST_MODE=1 \
        PISTICK_TEST_ROOT="$TEST_ROOT" \
        PISTICK_RELEASES_API_URL="file://${RELEASES_JSON}" \
        PISTICK_TMDB_TOKEN="test-tmdb-read-token" \
        PISTICK_SKIP_TMDB_VALIDATION=1 \
        "$@" \
        bash "$INSTALLER"
}

run_installed_updater() {
    env \
        PISTICK_TEST_MODE=1 \
        PISTICK_TEST_ROOT="$TEST_ROOT" \
        PISTICK_RELEASES_API_URL="file://${RELEASES_JSON}" \
        PISTICK_TMDB_TOKEN="test-tmdb-read-token" \
        PISTICK_SKIP_TMDB_VALIDATION=1 \
        "$@" \
        bash "${TEST_ROOT}/usr/local/bin/pistick-update"
}

mkdir -p "$TEST_ROOT" "$FIXTURES"
bash -n "$INSTALLER"
python3 -m py_compile "${PROJECT_DIR}/main.py"

# Verify main.py's external data paths without importing Qt.
python3 - "${PROJECT_DIR}/main.py" <<'PY'
import ast
import os
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding="utf-8")
tree = ast.parse(source)
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_runtime_file")
namespace = {"os": os, "Path": Path, "__file__": sys.argv[1]}
exec(compile(ast.Module(body=[function], type_ignores=[]), sys.argv[1], "exec"), namespace)
os.environ["PISTICK_STATE_PATH"] = "/var/lib/pistick/user-data.json"
assert namespace["_runtime_file"]("PISTICK_STATE_PATH", "pistick_state.json") == Path("/var/lib/pistick/user-data.json")
del os.environ["PISTICK_STATE_PATH"]
assert namespace["_runtime_file"]("PISTICK_STATE_PATH", "pistick_state.json").name == "pistick_state.json"
PY

release_one="$(make_release 101 v1.0.0 first)"
write_release_index 101 v1.0.0 "$release_one" 2026-08-10T10:00:00Z
run_installer

assert_symlink "${TEST_ROOT}/opt/pistick/current"
assert_file "${TEST_ROOT}/opt/pistick/current/main.py"
assert_file "${TEST_ROOT}/etc/pistick/config.json"
assert_file "${TEST_ROOT}/usr/local/bin/pistick-update"
assert_equals "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag_name"])' "${TEST_ROOT}/var/lib/pistick/installed-release.json")" "v1.0.0"
assert_equals "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tmdb_read_token"])' "${TEST_ROOT}/etc/pistick/config.json")" "test-tmdb-read-token"

printf '{"profiles":[{"id":"kept"}],"watch_state":{}}\n' >"${TEST_ROOT}/var/lib/pistick/user-data.json"
config_hash="$(sha256sum "${TEST_ROOT}/etc/pistick/config.json" | cut -d' ' -f1)"
state_hash="$(sha256sum "${TEST_ROOT}/var/lib/pistick/user-data.json" | cut -d' ' -f1)"

# A repeated manual run is idempotent.
run_installed_updater
assert_equals "$(sha256sum "${TEST_ROOT}/etc/pistick/config.json" | cut -d' ' -f1)" "$config_hash"
assert_equals "$(sha256sum "${TEST_ROOT}/var/lib/pistick/user-data.json" | cut -d' ' -f1)" "$state_hash"

# A newer published pre-release is installed; a newer draft is ignored.
release_two="$(make_release 202 v1.1.0-alpha second)"
write_release_index 202 v1.1.0-alpha "$release_two" 2026-08-11T10:00:00Z \
    101 v1.0.0 "$release_one" 2026-08-10T10:00:00Z
run_installed_updater
assert_equals "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag_name"])' "${TEST_ROOT}/var/lib/pistick/installed-release.json")" "v1.1.0-alpha"
assert_equals "$(python3 -c 'import runpy,sys; print(runpy.run_path(sys.argv[1])["MARKER"])' "${TEST_ROOT}/opt/pistick/current/main.py")" "second"
assert_equals "$(sha256sum "${TEST_ROOT}/etc/pistick/config.json" | cut -d' ' -f1)" "$config_hash"
assert_equals "$(sha256sum "${TEST_ROOT}/var/lib/pistick/user-data.json" | cut -d' ' -f1)" "$state_hash"

# A startup failure restores the prior release and metadata.
release_three="$(make_release 303 v1.2.0 third)"
write_release_index 303 v1.2.0 "$release_three" 2026-08-12T10:00:00Z \
    202 v1.1.0-alpha "$release_two" 2026-08-11T10:00:00Z
if run_installed_updater PISTICK_TEST_HEALTH_FAIL=1; then
    fail "A failed health check unexpectedly succeeded"
fi
assert_equals "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag_name"])' "${TEST_ROOT}/var/lib/pistick/installed-release.json")" "v1.1.0-alpha"
assert_equals "$(python3 -c 'import runpy,sys; print(runpy.run_path(sys.argv[1])["MARKER"])' "${TEST_ROOT}/opt/pistick/current/main.py")" "second"
assert_equals "$(sha256sum "${TEST_ROOT}/etc/pistick/config.json" | cut -d' ' -f1)" "$config_hash"
assert_equals "$(sha256sum "${TEST_ROOT}/var/lib/pistick/user-data.json" | cut -d' ' -f1)" "$state_hash"

# There is intentionally no automatic updater.
if find "$TEST_ROOT" -type f \( -name '*.timer' -o -path '*/cron.*/*' \) | grep -q .; then
    fail "The installer created an automatic update trigger"
fi
if grep -Eq 'git[[:space:]]+(pull|clone|checkout|fetch)' "$INSTALLER"; then
    fail "The installer contains a branch-based Git update command"
fi

printf 'All PiStick installer tests passed.\n'
