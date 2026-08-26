package app.pistick.android;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.junit.Test;

public final class FireTvReleaseTest {
    private static final String DIGEST =
            "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    @Test
    public void selectsNewestStableFireTvAssetAndIgnoresPhoneApks() throws Exception {
        String json = "["
                + release(false, false, "PiStick-AndroidV1.1.apk", 130000, DIGEST,
                "https://github.com/tnichol00/PiStick/releases/download/V1.1/PiStick-AndroidV1.1.apk")
                + "," + release(false, true, "PiStick-Fire-TV-v9.apk", 140000, DIGEST,
                "https://github.com/tnichol00/PiStick/releases/download/firetv-v9/PiStick-Fire-TV-v9.apk")
                + "," + release(false, false, "PiStick-Fire-TV-v3.apk", 140000, DIGEST,
                "https://github.com/tnichol00/PiStick/releases/download/firetv-v3/PiStick-Fire-TV-v3.apk")
                + "," + release(false, false, "PiStick-Fire-TV-v4.apk", 145000, DIGEST,
                "https://github.com/tnichol00/PiStick/releases/download/firetv-v4/PiStick-Fire-TV-v4.apk")
                + "]";

        FireTvRelease latest = FireTvRelease.findLatest(json, 2);

        assertEquals(4, latest.versionCode);
        assertEquals("PiStick-Fire-TV-v4.apk", latest.assetName);
        assertEquals("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                latest.sha256);
    }

    @Test
    public void rejectsUntrustedDownloadsAndMissingDigests() throws Exception {
        String json = "["
                + release(false, false, "PiStick-Fire-TV-v5.apk", 145000, DIGEST,
                "https://example.com/PiStick-Fire-TV-v5.apk")
                + "," + release(false, false, "PiStick-Fire-TV-v6.apk", 145000, "",
                "https://github.com/tnichol00/PiStick/releases/download/firetv-v6/PiStick-Fire-TV-v6.apk")
                + "," + release(false, false, "PiStick-Fire-TV-v7.apk", 145000, DIGEST,
                "not a valid URL")
                + "]";

        assertNull(FireTvRelease.findLatest(json, 2));
    }

    @Test
    public void rejectsMissingUploadStateAndExtraDownloadPathSegments() throws Exception {
        String missingState = "[{\"draft\":false,\"prerelease\":false,\"assets\":[{"
                + "\"name\":\"PiStick-Fire-TV-v5.apk\",\"size\":145000,"
                + "\"digest\":\"" + DIGEST + "\",\"browser_download_url\":"
                + "\"https://github.com/tnichol00/PiStick/releases/download/firetv-v5/"
                + "PiStick-Fire-TV-v5.apk\"}]}]";
        String extraPath = "[" + release(false, false, "PiStick-Fire-TV-v6.apk", 145000, DIGEST,
                "https://github.com/tnichol00/PiStick/releases/download/firetv-v6/extra/"
                        + "PiStick-Fire-TV-v6.apk") + "]";
        String mismatchedTag = "[" + release(false, false, "PiStick-Fire-TV-v7.apk", 145000,
                DIGEST, "https://github.com/tnichol00/PiStick/releases/download/firetv-v8/"
                        + "PiStick-Fire-TV-v7.apk") + "]";

        assertNull(FireTvRelease.findLatest(missingState, 2));
        assertNull(FireTvRelease.findLatest(extraPath, 2));
        assertNull(FireTvRelease.findLatest(mismatchedTag, 2));
    }

    @Test
    public void reportsCurrentWhenOnlyInstalledOrOlderVersionsExist() throws Exception {
        String json = "[" + release(false, false, "PiStick-Fire-TV-v3.apk", 140000, DIGEST,
                "https://github.com/tnichol00/PiStick/releases/download/firetv-v3/PiStick-Fire-TV-v3.apk")
                + "]";

        assertNull(FireTvRelease.findLatest(json, 3));
    }

    private static String release(
            boolean draft,
            boolean prerelease,
            String name,
            long size,
            String digest,
            String url
    ) {
        return "{\"draft\":" + draft
                + ",\"prerelease\":" + prerelease
                + ",\"name\":\"Test release\",\"assets\":[{\"state\":\"uploaded\""
                + ",\"name\":\"" + name + "\",\"size\":" + size
                + ",\"digest\":\"" + digest + "\",\"browser_download_url\":\""
                + url + "\"}]}";
    }
}
