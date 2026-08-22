# PiStick for Android

PiStick for Android is a self-contained Android application. It does not connect to a Raspberry Pi, start a Python process, bind a network port, host localhost pages, or require a PiStick server. The familiar PiStick interface is packaged in the APK, while profiles, watch state, TMDB requests, and secure credential storage are implemented in native Android code.

## Requirements

- Android 8.0 (API 26) or newer
- Internet access for TMDB images/metadata, trailers, and Videasy playback
- A free TMDB Read Access Token or v3 API key

The TMDB credential is validated directly with TMDB and encrypted with an AES-GCM key held by Android Keystore. It is excluded from Android backup and device-transfer data.

## Build

Open this `android` folder in Android Studio, or use the included Gradle wrapper:

```bash
./gradlew testDebugUnitTest lintDebug assembleDebug
```

The installable development APK is written to:

```text
app/build/outputs/apk/debug/app-debug.apk
```

For a distributable release, configure your own long-lived Android signing key and run `./gradlew assembleRelease`. Never commit the signing key or its passwords.

## Architecture

- `MainActivity`: hardened Android WebView host, navigation lock, fullscreen playback, and renderer-crash recovery
- `PiStickBridge` / `PiStickApi`: authenticated in-process interface used by the bundled UI
- `TmdbClient`: direct HTTPS TMDB client with parallel home loading and bounded in-memory caching
- `CredentialStore`: Android Keystore-backed AES-GCM credential encryption
- `StateStore`: atomic private JSON persistence for profiles, watch history, and resume positions
- `assets/web`: the packaged PiStick interface; these static files are loaded from the APK and are not served over HTTP

The bridge uses a random per-WebView secret that is injected only into the top-level bundled document. This prevents untrusted cross-origin player frames from invoking native app operations through Android's JavaScript bridge.
