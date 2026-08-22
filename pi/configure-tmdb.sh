#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPOSITORY="tnichol00/PiStick"
SOURCE_BRANCH="Pi-Zero-W"
TEST_MODE="${PISTICK_TEST_MODE:-0}"
TEST_ROOT="${PISTICK_TEST_ROOT:-}"
DATA_DIR="${TEST_ROOT}/var/lib/pistick/data"
CONFIG_PATH="${DATA_DIR}/config.json"

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  sudo pistick-configure-tmdb          Set or replace the TMDB token
  sudo pistick-configure-tmdb --check  Confirm that a token is saved
EOF
}

if [[ "$TEST_MODE" != "1" && ${EUID:-$(id -u)} -ne 0 ]]; then
    fail "Run this command with sudo: sudo pistick-configure-tmdb"
fi

case "${1:-}" in
    ""|--check) ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

if [[ ! -d "$DATA_DIR" ]]; then
    fail "PiStick is not fully installed: $DATA_DIR does not exist.
Run the Pi Zero W installer first:
  curl -fsSL https://raw.githubusercontent.com/${REPOSITORY}/refs/heads/${SOURCE_BRANCH}/install.sh -o /tmp/install-pistick.sh && sudo bash /tmp/install-pistick.sh"
fi

if [[ "${1:-}" == "--check" ]]; then
    if python3 - "$CONFIG_PATH" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8-sig") as source:
        data = json.load(source)
    token = str(data.get("tmdb_read_token") or data.get("tmdb_token") or "").strip()
except (OSError, ValueError, TypeError, AttributeError):
    token = ""

upper = token.upper()
valid = len(token) >= 24 and "PASTE_YOUR" not in upper and "KEEP_THE_QUOTES" not in upper
raise SystemExit(0 if valid else 1)
PY
    then
        printf 'TMDB token is set. The token itself was not displayed.\n'
        exit 0
    fi
    fail "No TMDB token is set. Add one with: sudo pistick-configure-tmdb"
fi

token="${PISTICK_TMDB_TOKEN:-}"
if [[ -z "$token" ]]; then
    [[ -r /dev/tty ]] || fail "Run this command interactively over SSH."
    printf 'Paste the long TMDB API Read Access Token (typing is hidden): ' >/dev/tty
    IFS= read -r -s token </dev/tty
    printf '\n' >/dev/tty
fi
if [[ ${#token} -lt 24 ]]; then
    fail "That does not look like the long TMDB API Read Access Token."
fi

PISTICK_SKIP_TMDB_VALIDATION="${PISTICK_SKIP_TMDB_VALIDATION:-0}" \
python3 - "$CONFIG_PATH" 3<<<"$token" <<'PY'
import json
import os
import secrets
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

path = sys.argv[1]
with os.fdopen(3, encoding="utf-8") as token_input:
    token = token_input.read().strip()
if os.getenv("PISTICK_SKIP_TMDB_VALIDATION") != "1":
    request = Request(
        "https://api.themoviedb.org/3/configuration",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": "PiStick-Pi-Config/2",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            if response.status != 200:
                raise RuntimeError("unexpected response")
    except HTTPError as error:
        if error.code in (401, 403):
            raise SystemExit("TMDB rejected that token. Copy the long API Read Access Token.")
        raise SystemExit("TMDB validation failed with HTTP " + str(error.code) + ".")
    except (URLError, TimeoutError, OSError, RuntimeError) as error:
        raise SystemExit("TMDB could not be reached: " + str(error))

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
temporary = path + ".tmp"
with open(temporary, "w", encoding="utf-8") as destination:
    json.dump(data, destination, indent=2)
    destination.write("\n")
os.replace(temporary, path)
PY

chmod 0600 "$CONFIG_PATH"
if [[ "$TEST_MODE" != "1" ]]; then
    chown --reference="$DATA_DIR" "$CONFIG_PATH"
    systemctl restart pistick-server.service
fi
printf 'TMDB token updated. PiStick Server restarted.\n'
