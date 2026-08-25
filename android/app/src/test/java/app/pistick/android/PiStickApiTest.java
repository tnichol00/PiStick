package app.pistick.android;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class PiStickApiTest {
    @Test
    public void moviePlaybackRequestsAutoplayAndResume() {
        assertEquals(
                "https://player.videasy.to/movie/550?autoplay=true&progress=125",
                PiStickApi.playbackUrl("/movie/550", 125.9)
        );
    }

    @Test
    public void episodePlaybackRequestsAutoplayAndAutoNext() {
        assertEquals(
                "https://player.videasy.to/tv/1399/1/2?autoplay=true&autoplayNextEpisode=true",
                PiStickApi.playbackUrl("/tv/1399/1/2", 0)
        );
    }
}
