# Install PiStick on Fire TV

## Requirements

- An Android-based Fire TV or Fire TV Stick running Fire OS 6 or newer
- An internet connection
- A free [TMDB account](https://www.themoviedb.org/signup)
- A phone or computer for downloading and transferring the APK

PiStick Fire TV is a standalone native APK. It does not need a Raspberry Pi or PiStick web server. Vega OS models, including Fire TV Stick 4K Select, cannot install Android APKs.

## 1. Download the Fire TV APK

For the stable, updateable build, open [PiStick Releases](https://github.com/tnichol00/PiStick/releases) and download the newest file named `PiStick-Fire-TV-vNUMBER.apk`. Do not use a file whose name says Android phone.

To test the newest development build from this branch instead:

1. Open the [`firetv` branch Actions page](https://github.com/tnichol00/PiStick/actions/workflows/firetv-ci.yml?query=branch%3Afiretv).
2. Open the newest successful **Fire TV CI** run.
3. Under **Artifacts**, download **PiStick-Fire-TV-debug**.
4. Unzip the download to get `PiStick-Fire-TV-debug.apk`.

Development APKs are debug-signed and may not update over a stable release.

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
adb install -r PiStick-Fire-TV-v3.apk
```

Replace `FIRE_TV_IP` with the address shown by Fire TV and replace the APK filename with the exact file you downloaded (`PiStick-Fire-TV-debug.apk` for a development build). Accept the debugging prompt on the television. If `adb install -r` reports an incompatible signature, uninstall the existing PiStick Fire TV build and install again; uninstalling removes that installation's profiles and watch history.

## 4. Open and set up PiStick

1. Open **Apps** on Fire TV and select **PiStick Fire TV**. If it is not on the main Apps row, use **Settings → Applications → Manage Installed Applications → PiStick Fire TV → Launch application** once.
2. Enter either the long TMDB **API Read Access Token** or the shorter TMDB v3 API key.
3. Select **Validate and save**, then choose or create a profile.

During playback, PiStick fills the screen automatically. **Select** or **Play/Pause** toggles playback, **Left/Right** skips 10 seconds, **Rewind/Fast-forward** skips five minutes, **Back** closes playback, and **Menu** toggles subtitles.

## Update PiStick Fire TV

Select **Update App** beside the Settings icon. PiStick checks the stable GitHub releases for a newer Fire TV APK, downloads it, verifies its GitHub SHA-256 digest, package name, version, and signing certificate, and then opens Fire OS's installer. The first update may ask you to allow PiStick under **Install unknown apps**. Confirm **Install** on the Fire TV screen.

Android replaces the old app code while retaining PiStick's configuration, profiles, and watch history. PiStick removes its obsolete downloaded update APKs the next time it starts.

If the installed copy is an older debug-signed build, the first stable APK can report an incompatible signature. This transition requires one uninstall and reinstall; uninstalling removes that installation's TMDB credential, profiles, and watch history. Stable releases signed with the same permanent key can update in place afterward.
