package app.pistick.android;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

final class WebAdBlocker {
    private static final Set<String> BLOCKED_HOSTS = Collections.unmodifiableSet(new HashSet<>(
            Arrays.asList(
                    "doubleclick.net",
                    "googlesyndication.com",
                    "googleadservices.com",
                    "adservice.google.com",
                    "google-analytics.com",
                    "googletagmanager.com",
                    "adnxs.com",
                    "adsrvr.org",
                    "amazon-adsystem.com",
                    "bidswitch.net",
                    "casalemedia.com",
                    "criteo.com",
                    "criteo.net",
                    "openx.net",
                    "outbrain.com",
                    "pubmatic.com",
                    "rubiconproject.com",
                    "smartadserver.com",
                    "taboola.com",
                    "yieldmo.com",
                    "moatads.com",
                    "scorecardresearch.com",
                    "quantserve.com",
                    "exoclick.com",
                    "exosrv.com",
                    "propellerads.com",
                    "popads.net",
                    "popcash.net",
                    "clickadu.com",
                    "juicyads.com",
                    "hilltopads.net",
                    "trafficjunky.net",
                    "adsterra.com",
                    "onclicka.com",
                    "pushground.com",
                    "evadav.com",
                    "monetag.com",
                    "hotjar.com",
                    "clarity.ms",
                    "segment.io",
                    "mixpanel.com",
                    "amplitude.com"
            )
    ));

    private static final String[] BLOCKED_PATH_MARKERS = {
            "/adserver", "/adserve", "/serveads", "/ads/", "/ads.",
            "/adtag", "/vast", "/vmap", "/prebid", "/preroll",
            "/popunder", "/clickunder"
    };

    private static final String[] BLOCKED_QUERY_MARKERS = {
            "adtag=", "ad_tag=", "vast=", "vmap=", "preroll=",
            "popunder=", "clickunder=", "ima_sdk="
    };

    private WebAdBlocker() {}

    static boolean shouldBlock(String rawUrl) {
        if (rawUrl == null || rawUrl.isEmpty()) return false;
        final URI uri;
        try {
            uri = new URI(rawUrl);
        } catch (URISyntaxException ignored) {
            return false;
        }

        String scheme = lower(uri.getScheme());
        if (!"http".equals(scheme) && !"https".equals(scheme)) return false;
        String host = lower(uri.getHost());
        if (host.endsWith(".")) host = host.substring(0, host.length() - 1);
        if (host.isEmpty()) return false;

        for (String blockedHost : BLOCKED_HOSTS) {
            if (host.equals(blockedHost) || host.endsWith("." + blockedHost)) return true;
        }

        String path = lower(uri.getRawPath());
        for (String marker : BLOCKED_PATH_MARKERS) {
            if (path.contains(marker)) return true;
        }

        String query = lower(uri.getRawQuery());
        for (String marker : BLOCKED_QUERY_MARKERS) {
            if (query.contains(marker)) return true;
        }
        return false;
    }

    private static String lower(String value) {
        return value == null ? "" : value.toLowerCase(Locale.ROOT);
    }
}
