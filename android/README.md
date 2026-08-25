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
