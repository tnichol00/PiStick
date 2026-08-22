# PiStick for Android

This branch contains the standalone Android edition of PiStick. It is an installable Android APK with the PiStick interface bundled inside the application and a native Android data/network layer.

There is no PiStick web server in this branch. The app does not run Python, bind a port, load its interface from localhost, connect to a Raspberry Pi, or depend on another PiStick installation.

## Features

- Same PiStick home, Movies, TV Shows, search, profiles, details, and player experience
- Direct HTTPS metadata requests to TMDB
- Videasy movie/episode playback and YouTube trailers in the in-app player
- Per-profile watch history, Continue Watching, and resume positions stored privately on the phone
- TMDB credential encrypted with an AES-GCM key held by Android Keystore
- Parallel home-screen loading plus bounded in-memory response caching
- Fullscreen playback, controller/keyboard handling, and WebView renderer-crash recovery
- No cleartext network traffic, pop-up windows, file chooser, camera, or microphone permission

## Install the provided APK

On an Android 8.0 or newer phone, allow APK installation for the app you use to open the file, then open `PiStick-Android.apk`. Android may label it as a development build because the downloadable test APK is debug-signed.

The first launch asks for a TMDB Read Access Token or v3 API key. PiStick validates it directly with TMDB and stores only the encrypted value on the device.

## Build from source

The Android Studio/Gradle project is in [`android`](android). With JDK 17 and Android SDK 35 installed:

```bash
cd android
./gradlew --no-daemon testDebugUnitTest lintDebug assembleDebug
```

The resulting installable APK is:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

For a production or Play Store build, configure your own long-lived Android signing key and build the release variant. Never commit the key or its passwords.

See [`android/README.md`](android/README.md) for the implementation and security architecture.
