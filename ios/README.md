# PiStick for iPhone and iPad

PiStick is a universal SwiftUI app for iOS and iPadOS 17 or newer. It does not start Python, listen on a port, or require a PiStick server. The bundled interface calls a native Swift API; Swift talks directly to TMDB and stores profiles and watch progress in the app's Application Support folder.

On first launch, PiStick asks for either a TMDB **API Read Access Token** (the long value normally beginning with `eyJ`) or a v3 API key. It validates the credential directly with TMDB, then stores it in the device-only Keychain. The bundled web interface never receives the saved credential.

An internet connection is still required for TMDB images/metadata, trailers, and the configured third-party playback provider. Profiles and history are local to each device and are not synchronized.

## Generate the project locally

The generated `.xcodeproj` is intentionally not committed. On a Mac with Xcode 26 and [XcodeGen](https://github.com/yonaskolb/XcodeGen):

```bash
cd ios
brew install xcodegen
xcodegen generate
open PiStick.xcodeproj
```

Choose your Apple Development team and a unique bundle identifier in Xcode, connect an iPhone or iPad, then press Run. The GitHub workflow below is the easier repeatable/TestFlight route.

## One-time GitHub and Apple setup

The workflow follows Loop's browser-build pattern: after one-time signing setup, build from **Actions → Build PiStick for iPhone and iPad → Run workflow**.

1. In [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/identifiers/list), register an explicit App ID such as `com.yourname.PiStick`.
2. In [App Store Connect](https://appstoreconnect.apple.com/), create a new iOS app named PiStick using the same bundle ID.
3. In App Store Connect, open **Users and Access → Integrations → App Store Connect API**, create a team API key with access that can upload builds and manage signing, download its `.p8` file, and copy its Key ID and Issuer ID. Apple only offers the private-key download once.
4. Create an empty **private** GitHub repository, for example `yourname/PiStick-Signing`. Fastlane Match will put encrypted certificates and profiles there.
5. Create a fine-grained GitHub personal access token that can read and write the signing repository's contents.
6. In the PiStick repository, open **Settings → Secrets and variables → Actions** and add these repository **variables**:

   - `PISTICK_BUNDLE_ID`: the exact App ID from step 1
   - `MATCH_REPOSITORY`: the signing repo as `owner/repository`

7. Add these repository **secrets**:

   - `APPLE_TEAM_ID`: the 10-character team ID shown in the Apple developer account
   - `APP_STORE_CONNECT_KEY_ID`: API key ID from step 3
   - `APP_STORE_CONNECT_ISSUER_ID`: issuer ID from step 3
   - `APP_STORE_CONNECT_PRIVATE_KEY`: the complete contents of the downloaded `.p8` file
   - `MATCH_PASSWORD`: a long password you choose; keep a backup because it encrypts the signing repository
   - `GH_PAT`: the GitHub token from step 5

8. Merge the iOS pull request into the repository's default branch. GitHub only shows a `workflow_dispatch` **Run workflow** button for workflows present on the default branch.

## Build and put it on your phone

1. Open the repository's **Actions** tab.
2. Select **Build PiStick for iPhone and iPad**.
3. Click **Run workflow**, leave **Upload the signed build to TestFlight** enabled, and run it from the default branch.
4. The first run creates the distribution certificate/profile in the encrypted signing repository, builds one universal IPA, and uploads it to App Store Connect. Later runs reuse signing and only need the same button.
5. Wait for Apple to process the build. In App Store Connect, open **PiStick → TestFlight**, add the build to an internal testing group, and add the Apple ID you use on the phone as an internal tester.
6. Install Apple's [TestFlight](https://apps.apple.com/app/testflight/id899247664) app on the iPhone or iPad, accept the invitation, and tap **Install** beside PiStick.
7. Launch PiStick and paste the TMDB Read Access Token or API key into the first-start prompt.

TestFlight builds normally expire after 90 days, so run the GitHub workflow again before the installed build expires. Distribution outside your own testing group can require beta review, content rights, privacy disclosures, and compliance with Apple, TMDB, and playback-provider terms.

## What the workflow does

- Uses GitHub's `macos-26` runner and Xcode 26.
- Generates the Xcode project from `project.yml`.
- Uses Fastlane Match to create/reuse encrypted App Store signing assets.
- Produces a signed universal `PiStick.ipa` and saves it as a short-lived workflow artifact.
- Uploads the build to TestFlight when the workflow checkbox is enabled.

The separate **iOS checks** workflow compiles/tests unsigned iPhone and iPad simulator builds and requires no Apple secrets.
