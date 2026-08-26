package app.pistick.android;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.URI;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class FireTvRelease {
    private static final Pattern ASSET_NAME = Pattern.compile("^PiStick-Fire-TV-v([1-9][0-9]*)\\.apk$");
    private static final Pattern DIGEST = Pattern.compile("^sha256:([0-9a-fA-F]{64})$");
    private static final long MINIMUM_APK_BYTES = 32L * 1024L;
    private static final long MAXIMUM_APK_BYTES = 50L * 1024L * 1024L;

    final int versionCode;
    final String versionName;
    final String assetName;
    final String downloadUrl;
    final String sha256;
    final long size;

    private FireTvRelease(
            int versionCode,
            String versionName,
            String assetName,
            String downloadUrl,
            String sha256,
            long size
    ) {
        this.versionCode = versionCode;
        this.versionName = versionName;
        this.assetName = assetName;
        this.downloadUrl = downloadUrl;
        this.sha256 = sha256;
        this.size = size;
    }

    static FireTvRelease findLatest(String json, int installedVersionCode) throws Exception {
        JSONArray releases = new JSONArray(json);
        FireTvRelease latest = null;
        for (int releaseIndex = 0; releaseIndex < releases.length(); releaseIndex++) {
            JSONObject release = releases.optJSONObject(releaseIndex);
            if (release == null || release.optBoolean("draft", false)
                    || release.optBoolean("prerelease", false)) continue;
            JSONArray assets = release.optJSONArray("assets");
            if (assets == null) continue;
            for (int assetIndex = 0; assetIndex < assets.length(); assetIndex++) {
                JSONObject asset = assets.optJSONObject(assetIndex);
                FireTvRelease candidate = parseAsset(release, asset);
                if (candidate == null || candidate.versionCode <= installedVersionCode) continue;
                if (latest == null || candidate.versionCode > latest.versionCode) latest = candidate;
            }
        }
        return latest;
    }

    private static FireTvRelease parseAsset(JSONObject release, JSONObject asset) {
        if (asset == null || !"uploaded".equals(asset.optString("state", ""))) return null;
        String name = asset.optString("name", "");
        Matcher nameMatch = ASSET_NAME.matcher(name);
        if (!nameMatch.matches()) return null;
        int versionCode;
        try {
            versionCode = Integer.parseInt(nameMatch.group(1));
        } catch (NumberFormatException error) {
            return null;
        }

        long size = asset.optLong("size", 0);
        if (size < MINIMUM_APK_BYTES || size > MAXIMUM_APK_BYTES) return null;
        Matcher digestMatch = DIGEST.matcher(asset.optString("digest", ""));
        if (!digestMatch.matches()) return null;

        String downloadUrl = asset.optString("browser_download_url", "");
        URI uri;
        try {
            uri = new URI(downloadUrl);
        } catch (java.net.URISyntaxException error) {
            return null;
        }
        String requiredPathPrefix = "/tnichol00/PiStick/releases/download/";
        String rawPath = uri.getRawPath();
        String releasePath = rawPath == null || !rawPath.startsWith(requiredPathPrefix)
                ? "" : rawPath.substring(requiredPathPrefix.length());
        int pathSeparator = releasePath.indexOf('/');
        String releaseTag = pathSeparator <= 0 ? "" : releasePath.substring(0, pathSeparator);
        if (!"https".equalsIgnoreCase(uri.getScheme())
                || !"github.com".equalsIgnoreCase(uri.getHost())
                || uri.getRawUserInfo() != null
                || (uri.getPort() != -1 && uri.getPort() != 443)
                || uri.getRawQuery() != null
                || uri.getRawFragment() != null
                || pathSeparator <= 0
                || !releaseTag.equals("firetv-v" + versionCode)
                || !releasePath.substring(pathSeparator + 1).equals(name)) return null;

        String releaseName = release.optString("name", "").trim();
        if (releaseName.isEmpty()) releaseName = release.optString("tag_name", "").trim();
        if (releaseName.isEmpty()) releaseName = "Fire TV v" + versionCode;
        return new FireTvRelease(
                versionCode,
                releaseName,
                name,
                downloadUrl,
                digestMatch.group(1).toLowerCase(Locale.ROOT),
                size
        );
    }
}
