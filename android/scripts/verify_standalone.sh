#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
manifest="$project_root/app/src/main/AndroidManifest.xml"
source_root="$project_root/app/src/main"

test -f "$source_root/assets/web/index.html"
test -f "$source_root/assets/web/app.js"
grep -q 'window.PiStickAndroid' "$source_root/assets/web/app.js"
grep -q 'PISTICK_ANDROID_SECRET' "$source_root/assets/web/app.js"
if grep -q -E 'window\.webkit|fetch\(' "$source_root/assets/web/app.js"; then
  echo "A non-Android or web-server API fallback was found in the bundled interface." >&2
  exit 1
fi
grep -q 'usesCleartextTraffic="false"' "$manifest"
grep -q 'android.permission.INTERNET' "$manifest"

if grep -R -E -n 'ServerSocket|NanoHTTPD|localhost:[0-9]|127\.0\.0\.1:[0-9]|python(3)? -m http|Flask|FastAPI' "$source_root"; then
  echo "A web-server implementation or localhost dependency was found in the Android app." >&2
  exit 1
fi

echo "Android standalone checks passed: bundled UI, native bridge, and no web server."
