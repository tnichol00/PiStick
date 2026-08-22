package app.pistick.android;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;

public final class StateStoreTest {
    @Rule
    public TemporaryFolder temporaryFolder = new TemporaryFolder();

    private StateStore store(File directory) {
        return new StateStore(new File(directory, "state.json"));
    }

    @Test
    public void defaultProfileAndProfileLifecyclePersist() throws Exception {
        File directory = temporaryFolder.newFolder("profiles");
        StateStore state = store(directory);
        assertEquals(1, state.profiles().length());
        assertNull(state.activeProfileId());

        JSONObject added = state.addProfile("Living Room");
        String id = added.getString("id");
        state.activateProfile(id);
        assertEquals(id, state.activeProfileId());
        assertEquals("Family", state.renameProfile(id, "Family").getString("name"));

        StateStore reloaded = store(directory);
        assertEquals(id, reloaded.activeProfileId());
        assertEquals("Family", reloaded.profile(id).getString("name"));
        reloaded.deleteProfile(id);
        assertNull(reloaded.activeProfileId());
        assertEquals(1, reloaded.profiles().length());
    }

    @Test
    public void movieProgressAppearsInContinueWatching() throws Exception {
        StateStore state = store(temporaryFolder.newFolder("movie"));
        String profileId = state.profiles().getJSONObject(0).getString("id");
        state.activateProfile(profileId);
        JSONObject movie = new JSONObject()
                .put("id", 550)
                .put("media_type", "movie")
                .put("title", "Example Movie")
                .put("poster_path", "/poster.jpg");

        state.setPosition(profileId, movie, 300, 1000);
        JSONArray continuing = state.continueWatching(profileId);
        assertEquals(1, continuing.length());
        assertEquals(0.3, continuing.getJSONObject(0).getJSONObject("watch").getDouble("progress"), 0.001);

        state.markFinished(profileId, movie);
        assertEquals(0, state.continueWatching(profileId).length());
    }

    @Test
    public void finishedEpisodeResumesAtNextEpisode() throws Exception {
        StateStore state = store(temporaryFolder.newFolder("episode"));
        String profileId = state.profiles().getJSONObject(0).getString("id");
        state.activateProfile(profileId);
        JSONObject show = new JSONObject()
                .put("id", 1399)
                .put("media_type", "tv")
                .put("name", "Example Show")
                .put("seasons", new JSONArray().put(
                        new JSONObject().put("season_number", 1).put("episode_count", 3)
                ));
        JSONObject episode = new JSONObject()
                .put("id", 1)
                .put("season_number", 1)
                .put("episode_number", 1)
                .put("name", "Episode 1");

        state.markEpisodeFinished(profileId, show, episode);
        int[] resume = state.resumeEpisode(profileId, show);
        assertEquals(1, resume[0]);
        assertEquals(2, resume[1]);
        assertTrue(state.continueWatching(profileId).length() == 1);
    }

    @Test(expected = PiStickException.class)
    public void lastProfileCannotBeDeleted() throws Exception {
        StateStore state = store(temporaryFolder.newFolder("last-profile"));
        String profileId = state.profiles().getJSONObject(0).getString("id");
        state.deleteProfile(profileId);
    }
}
