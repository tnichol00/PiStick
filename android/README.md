# Install a locally built PiStick Fire TV APK

## Requirements

- JDK 17
- Android SDK Platform 36 and Build Tools 35.0.0 or newer
- An Android-based Fire TV device running Fire OS 6 (API 25) or newer
- ADB from Android SDK Platform Tools

## 1. Build the APK

From this `android` directory, run:

```bash
./gradlew testDebugUnitTest lintDebug assembleDebug
```

The installable APK is created at `app/build/outputs/apk/debug/app-debug.apk`.

## 2. Prepare Fire TV

1. Open **Settings → My Fire TV → About**.
2. Highlight the device name and press **Select** seven times if **Developer Options** is hidden.
3. Open **Developer Options** and turn on **ADB Debugging**.
4. Find the device address under **About → Network**.

## 3. Install and launch

```bash
adb connect FIRE_TV_IP:5555
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell monkey -p app.pistick.firetv -c android.intent.category.LEANBACK_LAUNCHER 1
```

Replace `FIRE_TV_IP` with the address displayed by Fire TV and approve the debugging prompt on the television.

## Publish an updateable release

Fire TV releases must always use one permanent Android signing key. Configure these GitHub Actions repository secrets:

- `FIRETV_KEYSTORE_BASE64`: the base64-encoded JKS file
- `FIRETV_KEYSTORE_PASSWORD`: the keystore password
- `FIRETV_KEY_ALIAS`: the key alias
- `FIRETV_KEY_PASSWORD`: the key password

Increase `versionCode` and `versionName` in `app/build.gradle`, commit the change to `firetv`, and create a tag whose numeric suffix exactly equals the Android version code. For example, version code 3 uses:

```bash
git tag firetv-v3
git push origin firetv-v3
```

The Fire TV Release workflow tests, lints, signs, verifies, and publishes `PiStick-Fire-TV-v3.apk`. The in-app updater only accepts stable release assets following that exact naming pattern, whose package name, version, SHA-256 digest, and signing certificate all pass verification.

Do not replace or regenerate the signing key. Android will reject an in-place update signed by a different key. A device currently running a debug-signed APK needs one uninstall and reinstall of the first permanently signed release; later releases then update in place.
