package app.pistick.android;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class WebAdBlockerTest {
    @Test
    public void blocksKnownAdNetworksAndTheirSubdomains() {
        assertTrue(WebAdBlocker.shouldBlock("https://securepubads.g.doubleclick.net/tag"));
        assertTrue(WebAdBlocker.shouldBlock("https://cdn.exosrv.com/script.js"));
        assertTrue(WebAdBlocker.shouldBlock("https://ads.pubmatic.com/openrtb"));
    }

    @Test
    public void blocksEmbeddedAdAndPopupEndpoints() {
        assertTrue(WebAdBlocker.shouldBlock("https://player.example/vast?slot=pre"));
        assertTrue(WebAdBlocker.shouldBlock("https://player.example/start?adtag=https%3A%2F%2Fads.example"));
        assertTrue(WebAdBlocker.shouldBlock("https://player.example/popunder/launch"));
    }

    @Test
    public void preservesRequiredMediaAndMetadataTraffic() {
        assertFalse(WebAdBlocker.shouldBlock(
                "https://player.videasy.to/movie/550?autoplay=true&progress=12"));
        assertFalse(WebAdBlocker.shouldBlock("https://www.youtube.com/embed/abc123?autoplay=1"));
        assertFalse(WebAdBlocker.shouldBlock("https://rr1---sn.example.googlevideo.com/videoplayback?id=1"));
        assertFalse(WebAdBlocker.shouldBlock("https://image.tmdb.org/t/p/w342/poster.jpg"));
    }

    @Test
    public void requiresARealHostBoundaryAndAvoidsWordFalsePositives() {
        assertFalse(WebAdBlocker.shouldBlock("https://doubleclick.net.example.com/content"));
        assertFalse(WebAdBlocker.shouldBlock("https://example.com/movies/adventure"));
        assertFalse(WebAdBlocker.shouldBlock("about:blank"));
    }
}
