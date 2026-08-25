#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
manifest="$project_root/app/src/main/AndroidManifest.xml"
build_file="$project_root/app/build.gradle"
source_root="$project_root/app/src/main"
remote_source="$source_root/java/app/pistick/android/FireTvRemote.java"
activity_source="$source_root/java/app/pistick/android/MainActivity.java"
web_app="$source_root/assets/web/app.js"

require_text() {
  local file="$1"
  local expected="$2"
  if ! grep --fixed-strings --quiet -- "$expected" "$file"; then
    echo "Missing required Fire TV setting in ${file#$project_root/}: $expected" >&2
    exit 1
  fi
}

require_text "$manifest" 'android.intent.category.LEANBACK_LAUNCHER'
require_text "$manifest" 'android:banner="@drawable/pistick_tv_banner"'
require_text "$manifest" 'android.hardware.touchscreen" android:required="false"'
require_text "$manifest" 'android.hardware.faketouch" android:required="false"'
require_text "$manifest" 'android.software.leanback" android:required="false"'
require_text "$manifest" 'android:screenOrientation="landscape"'
require_text "$manifest" 'android:usesCleartextTraffic="false"'
require_text "$build_file" 'applicationId "app.pistick.firetv"'
require_text "$build_file" 'minSdk 25'
require_text "$build_file" 'targetSdk 36'
require_text "$remote_source" 'KEYCODE_DPAD_CENTER'
require_text "$remote_source" 'KEYCODE_MEDIA_PLAY_PAUSE'
require_text "$remote_source" 'KEYCODE_MEDIA_FAST_FORWARD'
require_text "$activity_source" 'window.PiStickFireTV'
require_text "$web_app" 'window.PiStickFireTV'
require_text "$web_app" 'focusRoot()'

if grep --recursive --extended-regexp --quiet 'java\.nio\.file' "$source_root/java"; then
  echo "API 26-only java.nio.file usage prevents Fire OS 6 support." >&2
  exit 1
fi

if grep --recursive --extended-regexp --quiet 'ServerSocket|NanoHTTPD|localhost:[0-9]|127\.0\.0\.1:[0-9]|python(3)? -m http|Flask|FastAPI' "$source_root"; then
  echo "A web server or localhost dependency was found in the standalone Fire TV app." >&2
  exit 1
fi

node --check "$web_app"

if [[ $# -gt 0 ]]; then
  apk="$1"
  test -f "$apk"
  sdk_root="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"
  if [[ -z "$sdk_root" ]]; then
    echo "ANDROID_SDK_ROOT or ANDROID_HOME is required for APK verification." >&2
    exit 1
  fi
  aapt="$(find "$sdk_root/build-tools" -type f -name aapt -print | sort -V | tail -1)"
  apksigner="$(find "$sdk_root/build-tools" -type f -name apksigner -print | sort -V | tail -1)"
  test -x "$aapt"
  test -x "$apksigner"
  manifest_dump="$(mktemp)"
  badging_dump="$(mktemp)"
  trap 'rm -f "$manifest_dump" "$badging_dump"' EXIT
  "$aapt" dump xmltree "$apk" AndroidManifest.xml > "$manifest_dump"
  "$aapt" dump badging "$apk" > "$badging_dump"
  require_text "$manifest_dump" 'android.intent.category.LEANBACK_LAUNCHER'
  require_text "$badging_dump" "package: name='app.pistick.firetv'"
  require_text "$badging_dump" "sdkVersion:'25'"
  require_text "$badging_dump" "targetSdkVersion:'36'"
  "$apksigner" verify --verbose "$apk"
  unzip -tq "$apk"
fi

echo "Fire TV checks passed: TV launcher, remote controls, API 25 compatibility, bundled UI, and no web server."
