# Install PiStick for Android

## Requirements

- A phone or tablet running Android 8.0 or newer
- An internet connection
- A free [TMDB account](https://www.themoviedb.org/signup)

## 1. Download the APK

1. Open the [latest PiStick release](https://github.com/tnichol00/PiStick/releases/latest) on the Android device.
2. Under **Assets**, download the file whose name ends in `.apk`.

The current V1.1 file is named [`PiStick-AndroidV1.1.apk`](https://github.com/tnichol00/PiStick/releases/download/V1.1/PiStick-AndroidV1.1.apk). Its size of approximately 0.13 MB is expected because PiStick uses the WebView already built into Android.

## 2. Allow APK installation

1. Open the downloaded APK from the browser notification or the **Downloads** folder.
2. If Android blocks it, select **Settings** and enable **Allow from this source** for the browser or Files app being used.
3. Return to the installer and select **Install**.

Android may describe PiStick as a development or externally downloaded app because this APK is installed outside Google Play.

## 3. Complete first-time setup

1. Open **PiStick**.
2. Enter either the long TMDB **API Read Access Token** or the shorter TMDB v3 API key.
3. Select **Save and Continue**.

PiStick validates the credential with TMDB and then opens the profile screen.

## Update PiStick

Download the newest APK from the [Releases page](https://github.com/tnichol00/PiStick/releases) and open it. Android normally offers to update the existing installation while retaining its data.

If Android reports that the update cannot be installed because its signature differs, uninstall the existing PiStick app before installing the new APK. Uninstalling also removes the profiles and watch history stored by that installation.
