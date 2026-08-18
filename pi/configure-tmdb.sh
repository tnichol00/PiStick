#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

CONFIG_PATH="/var/lib/pistick/data/config.json"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    printf 'Run this command with sudo: sudo pistick-configure-tmdb\n' >&2
    exit 1
fi
if [[ ! -t 0 ]]; then
    printf 'Run this command interactively over SSH.\n' >&2
    exit 1
fi

printf 'Paste the long TMDB API Read Access Token (typing is hidden): '
IFS= read -r -s token
printf '\n'
if [[ ${#token} -lt 24 ]]; then
    printf 'That does not look like the long TMDB Read Access Token.\n' >&2
    exit 1
fi

python3 - "$CONFIG_PATH" 3<<<"$token" <<'PY'
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

path = sys.argv[1]
with os.fdopen(3, encoding="utf-8") as token_input:
    token = token_input.read().strip()
request = Request(
    "https://api.themoviedb.org/3/configuration",
    headers={"Authorization": "Bearer " + token, "Accept": "application/json", "User-Agent": "PiStick-Pi-Config/1"},
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
temporary = path + ".tmp"
with open(temporary, "w", encoding="utf-8") as destination:
    json.dump(data, destination, indent=2)
    destination.write("\n")
os.replace(temporary, path)
PY

chmod 0600 "$CONFIG_PATH"
chown --reference="$(dirname "$CONFIG_PATH")" "$CONFIG_PATH"
systemctl restart pistick-server.service
printf 'TMDB token updated. PiStick Server restarted.\n'
