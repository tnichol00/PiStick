#!/usr/bin/env bash

set -euo pipefail

app_path="${1:-}"

fail() {
  printf 'App bundle verification failed: %s\n' "$1" >&2
  exit 1
}

require_file() {
  [[ -f "$app_path/$1" ]] || fail "missing $1"
}

[[ -n "$app_path" ]] || fail "no .app path was provided"
[[ -d "$app_path" ]] || fail "app bundle does not exist at $app_path"

require_file "Info.plist"
require_file "Assets.car"
require_file "PrivacyInfo.xcprivacy"
require_file "Web/index.html"
require_file "Web/app.js"
require_file "Web/styles.css"

plist="$app_path/Info.plist"
iphone_icon_name="$(/usr/libexec/PlistBuddy -c \
  'Print :CFBundleIcons:CFBundlePrimaryIcon:CFBundleIconName' "$plist" 2>/dev/null || true)"
ipad_icon_name="$(/usr/libexec/PlistBuddy -c \
  'Print :CFBundleIcons~ipad:CFBundlePrimaryIcon:CFBundleIconName' "$plist" 2>/dev/null || true)"

[[ "$iphone_icon_name" == "AppIcon" ]] || fail "iPhone CFBundleIconName is not AppIcon"
[[ "$ipad_icon_name" == "AppIcon" ]] || fail "iPad CFBundleIconName is not AppIcon"

has_png_size() {
  local requested_size="$1"
  local image_path dimensions width height

  while IFS= read -r -d '' image_path; do
    dimensions="$(sips -g pixelWidth -g pixelHeight "$image_path" 2>/dev/null || true)"
    width="$(awk '/pixelWidth:/ { print $2 }' <<< "$dimensions")"
    height="$(awk '/pixelHeight:/ { print $2 }' <<< "$dimensions")"
    if [[ "$width" == "$requested_size" && "$height" == "$requested_size" ]]; then
      return 0
    fi
  done < <(find "$app_path" -maxdepth 1 -type f -name '*.png' -print0)

  return 1
}

has_png_size 120 || fail "missing the required 120x120 iPhone app icon"
has_png_size 152 || fail "missing the required 152x152 iPad app icon"

printf 'Verified resources and app icons in %s\n' "$app_path"
