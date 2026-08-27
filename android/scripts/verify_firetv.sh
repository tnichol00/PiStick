#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
manifest="$project_root/app/src/main/AndroidManifest.xml"
build_file="$project_root/app/build.gradle"
source_root="$project_root/app/src/main"
remote_source="$source_root/java/app/pistick/android/FireTvRemote.java"
activity_source="$source_root/java/app/pistick/android/MainActivity.java"
ad_blocker_source="$source_root/java/app/pistick/android/WebAdBlocker.java"
bridge_source="$source_root/java/app/pistick/android/PiStickBridge.java"
release_source="$source_root/java/app/pistick/android/FireTvRelease.java"
updater_source="$source_root/java/app/pistick/android/FireTvUpdater.java"
web_app="$source_root/assets/web/app.js"
web_index="$source_root/assets/web/index.html"
web_styles="$source_root/assets/web/styles.css"

require_text() {
  local file="$1"
  local expected="$2"
  if ! grep --fixed-strings --quiet -- "$expected" "$file"; then
    echo "Missing required Fire TV setting in ${file#$project_root/}: $expected" >&2
    exit 1
  fi
}

require_text "$manifest" 'android.intent.category.LEANBACK_LAUNCHER'
require_text "$manifest" 'android.permission.REQUEST_INSTALL_PACKAGES'
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
require_text "$remote_source" 'LONG_SEEK_SECONDS = 5 * 60'
require_text "$remote_source" 'SEEK_STEP_SECONDS = 10'
require_text "$activity_source" 'window.PiStickFireTV'
require_text "$activity_source" 'APP_ORIGIN = "https://" + APP_HOST'
require_text "$activity_source" 'shouldInterceptRequest'
require_text "$activity_source" 'showSoftKeyboard()'
require_text "$activity_source" 'InputMethodManager.SHOW_FORCED'
require_text "$activity_source" 'WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_VISIBLE'
require_text "$activity_source" 'public boolean onCreateWindow('
if [[ "$(grep --fixed-strings --count -- 'public boolean onCreateWindow(' "$activity_source")" -ne 1 ]]; then
  echo "Exactly one popup-blocking WebView handler is required." >&2
  exit 1
fi
require_text "$activity_source" 'WebAdBlocker.shouldBlock(url.toString())'
require_text "$ad_blocker_source" 'doubleclick.net'
require_text "$ad_blocker_source" 'popunder='
require_text "$activity_source" 'requestPlayerAutostart()'
require_text "$activity_source" 'void seekPlayer(int offsetSeconds)'
require_text "$activity_source" 'dispatchFullscreenPlayerEvent(event, action)'
require_text "$activity_source" 'seekCustomView(-FireTvRemote.LONG_SEEK_SECONDS)'
require_text "$activity_source" 'seekCustomView(FireTvRemote.LONG_SEEK_SECONDS)'
require_text "$activity_source" 'new FireTvUpdater(this)'
require_text "$bridge_source" 'public void checkForUpdates(String candidateSecret)'
require_text "$bridge_source" 'public void seekPlayer(String candidateSecret, int offsetSeconds)'
require_text "$release_source" 'PiStick-Fire-TV-v([1-9][0-9]*)'
require_text "$release_source" '"https".equalsIgnoreCase(uri.getScheme())'
require_text "$updater_source" 'api.github.com/repos/tnichol00/PiStick/releases'
require_text "$updater_source" 'canRequestPackageInstalls()'
require_text "$updater_source" '@SuppressLint("UnspecifiedRegisterReceiverFlag")'
require_text "$updater_source" '!installedSigners.equals(archiveSigners)'
require_text "$web_app" 'window.PiStickFireTV'
require_text "$web_app" 'focusRoot()'
require_text "$web_app" 'moveRailFocus(current, direction)'
require_text "$web_app" 'nativeUi("showKeyboard")'
require_text "$web_app" 'input.addEventListener("click"'
require_text "$web_app" 'state.currentView === "profiles" && !state.manageProfiles'
if [[ "$(grep --fixed-strings --count -- 'openSettings(false)' "$web_app")" -ne 2 ]]; then
  echo "Settings must only open from profile selection or its Fire TV Menu shortcut." >&2
  exit 1
fi
require_text "$web_app" 'nativeUi("requestPlayerAutostart")'
require_text "$web_app" 'enablejsapi=1&playsinline=1&origin='
require_text "$web_app" 'FIRE_TV ? "w342" : "w500"'
require_text "$web_app" 'nativePlayerSeek(-300)'
require_text "$web_app" 'nativePlayerSeek(300)'
require_text "$web_index" 'id="update-button"'
require_text "$web_index" 'class="settings-update-row"'
if grep --fixed-strings --quiet -- 'id="settings-button"' "$web_index"; then
  echo "Settings must only be exposed from the profile-selection screen." >&2
  exit 1
fi
require_text "$web_styles" '.fire-tv .player-toolbar,'
require_text "$web_styles" 'width: 100vw;'
require_text "$web_styles" 'height: 100vh;'

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

echo "Fire TV checks passed: fullscreen playback, exact remote controls, verified updates, API 25 compatibility, bundled UI, and no web server."
