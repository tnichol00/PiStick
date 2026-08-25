# Install PiStick on Fire TV

## Requirements

- An Android-based Fire TV or Fire TV Stick running Fire OS 6 or newer
- An internet connection
- A free [TMDB account](https://www.themoviedb.org/signup)
- A phone or computer for downloading and transferring the APK

PiStick Fire TV is a standalone native APK. It does not need a Raspberry Pi or PiStick web server. Vega OS models, including Fire TV Stick 4K Select, cannot install Android APKs.

## 1. Download the Fire TV APK

To use the build made automatically from this branch:

1. Open the [`firetv` branch Actions page](https://github.com/tnichol00/PiStick/actions/workflows/firetv-ci.yml?query=branch%3Afiretv).
2. Open the newest successful **Fire TV CI** run.
3. Under **Artifacts**, download **PiStick-Fire-TV-debug**.
4. Unzip the download to get `PiStick-Fire-TV-debug.apk`.

If a Fire TV APK has been added to [PiStick Releases](https://github.com/tnichol00/PiStick/releases), use that release APK instead. Do not use an APK whose name says Android phone.

## 2. Enable Fire TV developer options

1. On Fire TV, open **Settings → My Fire TV → About**.
2. Highlight the device name and press **Select** seven times if **Developer Options** is hidden.
3. Return to **My Fire TV → Developer Options**.
4. Turn on **ADB Debugging**.
5. Open **Install Unknown Apps** and allow the sideloading app you plan to use, if Fire TV shows this option.

## 3. Install with ADB

1. Find the Fire TV address under **Settings → My Fire TV → About → Network**.
2. Install [Android SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools) on the computer.
3. In a terminal opened in the folder containing the APK, run:

```bash
adb connect FIRE_TV_IP:5555
adb install -r PiStick-Fire-TV-debug.apk
```

Replace `FIRE_TV_IP` with the address shown by Fire TV. Accept the debugging prompt on the television. If `adb install -r` reports an incompatible signature, uninstall the existing PiStick Fire TV build and install again; uninstalling removes that installation's profiles and watch history.

## 4. Open and set up PiStick

1. Open **Apps** on Fire TV and select **PiStick Fire TV**. If it is not on the main Apps row, use **Settings → Applications → Manage Installed Applications → PiStick Fire TV → Launch application** once.
2. Enter either the long TMDB **API Read Access Token** or the shorter TMDB v3 API key.
3. Select **Validate and save**, then choose or create a profile.

Use the D-pad to move, **Select** to open or play/pause, **Back** to close a screen, and **Menu** to toggle subtitles during playback.

## Update PiStick Fire TV

Download the newer Fire TV APK and run the same `adb install -r` command. Android retains the app configuration when the package and signing key match.
