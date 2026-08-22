package app.pistick.android;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

final class StateStore {
    static final String[] AVATARS = {"red", "blue", "green", "purple", "orange", "teal"};
    static final int MAXIMUM_PROFILES = 8;

    private final File file;
    private JSONObject data;

    StateStore(Context context) {
        this(new File(context.getFilesDir(), "pistick_state.json"));
    }

    StateStore(File file) {
        this.file = file;
        data = read(file);
        normalize();
        try {
            save();
        } catch (PiStickException ignored) {
            // The first mutating request reports a storage error if persistence is unavailable.
        }
    }

    synchronized JSONObject profilesPayload() {
        JSONObject result = new JSONObject();
        JsonUtils.put(result, "profiles", profiles());
        String active = activeProfileId();
        JsonUtils.put(result, "active_profile", active == null ? JSONObject.NULL : active);
        JsonUtils.put(result, "max_profiles", MAXIMUM_PROFILES);
        return result;
    }

    synchronized JSONArray profiles() {
        return JsonUtils.copy(data.optJSONArray("profiles"));
    }

    synchronized String activeProfileId() {
        String candidate = JsonUtils.string(data.opt("active_profile"));
        return profile(candidate) == null ? null : candidate;
    }

    synchronized JSONObject profile(String id) {
        if (id == null || id.trim().isEmpty()) return null;
        JSONArray profiles = data.optJSONArray("profiles");
        if (profiles == null) return null;
        for (int index = 0; index < profiles.length(); index++) {
            JSONObject candidate = profiles.optJSONObject(index);
            if (candidate != null && id.equals(candidate.optString("id"))) return JsonUtils.copy(candidate);
        }
        return null;
    }

    synchronized JSONObject activateProfile(String id) throws PiStickException {
        JSONObject selected = requireProfile(id);
        JsonUtils.put(data, "active_profile", id);
        JSONObject watch = watchState();
        if (watch.optJSONObject(id) == null) JsonUtils.put(watch, id, new JSONObject());
        JsonUtils.put(data, "watch_state", watch);
        save();
        return selected;
    }

    synchronized JSONObject addProfile(String name) throws PiStickException {
        JSONArray current = profiles();
        if (current.length() >= MAXIMUM_PROFILES) {
            throw new PiStickException("PiStick supports up to " + MAXIMUM_PROFILES + " profiles.");
        }
        String cleaned = cleanName(name, "Profile " + (current.length() + 1));
        JSONObject item = new JSONObject();
        JsonUtils.put(item, "id", "profile-" + UUID.randomUUID().toString().replace("-", "").substring(0, 10));
        JsonUtils.put(item, "name", cleaned);
        JsonUtils.put(item, "avatar", AVATARS[current.length() % AVATARS.length]);
        current.put(item);
        JsonUtils.put(data, "profiles", current);
        JSONObject watch = watchState();
        JsonUtils.put(watch, item.optString("id"), new JSONObject());
        JsonUtils.put(data, "watch_state", watch);
        save();
        return JsonUtils.copy(item);
    }

    synchronized JSONObject renameProfile(String id, String name) throws PiStickException {
        requireProfile(id);
        String cleaned = truncate(name == null ? "" : name.trim(), 40);
        if (cleaned.isEmpty()) throw new PiStickException("Profile names cannot be blank.");
        JSONArray current = profiles();
        for (int index = 0; index < current.length(); index++) {
            JSONObject candidate = current.optJSONObject(index);
            if (candidate != null && id.equals(candidate.optString("id"))) {
                JsonUtils.put(candidate, "name", cleaned);
                JsonUtils.put(data, "profiles", current);
                save();
                return JsonUtils.copy(candidate);
            }
        }
        throw new PiStickException("Choose a valid profile first.");
    }

    synchronized void deleteProfile(String id) throws PiStickException {
        requireProfile(id);
        JSONArray current = profiles();
        if (current.length() <= 1) throw new PiStickException("PiStick must keep at least one profile.");
        boolean wasActive = id.equals(JsonUtils.string(data.opt("active_profile")));
        JSONArray retained = new JSONArray();
        for (int index = 0; index < current.length(); index++) {
            JSONObject candidate = current.optJSONObject(index);
            if (candidate != null && !id.equals(candidate.optString("id"))) retained.put(candidate);
        }
        JsonUtils.put(data, "profiles", retained);
        JSONObject watch = watchState();
        watch.remove(id);
        JsonUtils.put(data, "watch_state", watch);
        if (wasActive) JsonUtils.put(data, "active_profile", JSONObject.NULL);
        save();
    }

    synchronized JSONObject entry(String profileId, JSONObject media) throws PiStickException {
        return JsonUtils.copy(history(profileId).optJSONObject(mediaKey(media)));
    }

    synchronized JSONObject decorate(String profileId, JSONObject media) throws PiStickException {
        JSONObject result = JsonUtils.copy(media);
        JSONObject saved = history(profileId).optJSONObject(mediaKey(media));
        if (saved != null) {
            JsonUtils.put(result, "watch", JsonUtils.selected(saved,
                    "status", "progress", "position_seconds", "duration_seconds", "updated_at", "last_episode"));
        }
        return result;
    }

    synchronized void markStarted(String profileId, JSONObject media) throws PiStickException {
        JSONObject history = history(profileId);
        String key = mediaKey(media);
        JSONObject previous = history.optJSONObject(key);
        if (previous == null) previous = new JSONObject();
        double oldProgress = JsonUtils.number(previous.opt("progress"), 0);
        double progress = "finished".equals(previous.optString("status")) ? 0.03 : Math.max(0.03, oldProgress);
        JSONObject value = new JSONObject();
        JsonUtils.put(value, "status", "in_progress");
        JsonUtils.put(value, "progress", Math.min(progress, 0.97));
        JsonUtils.put(value, "updated_at", now());
        JsonUtils.put(value, "media", snapshot(media));
        copyIfPresent(previous, value, "position_seconds", "duration_seconds");
        JsonUtils.put(history, key, value);
        setHistory(profileId, history);
    }

    synchronized void markFinished(String profileId, JSONObject media) throws PiStickException {
        JSONObject history = history(profileId);
        String key = mediaKey(media);
        JSONObject previous = history.optJSONObject(key);
        if (previous == null) previous = new JSONObject();
        JSONObject mediaSnapshot = snapshot(media);
        if (mediaSnapshot.length() == 0) mediaSnapshot = JsonUtils.copy(previous.optJSONObject("media"));
        JSONObject value = new JSONObject();
        JsonUtils.put(value, "status", "finished");
        JsonUtils.put(value, "progress", 1.0);
        JsonUtils.put(value, "updated_at", now());
        JsonUtils.put(value, "media", mediaSnapshot);
        if (previous.optJSONObject("episodes") != null) {
            JsonUtils.put(value, "episodes", previous.optJSONObject("episodes"));
        }
        JsonUtils.put(history, key, value);
        setHistory(profileId, history);
    }

    synchronized void markUnwatched(String profileId, JSONObject media) throws PiStickException {
        JSONObject history = history(profileId);
        history.remove(mediaKey(media));
        setHistory(profileId, history);
    }

    synchronized void setPosition(
            String profileId,
            JSONObject media,
            double positionSeconds,
            double durationSeconds
    ) throws PiStickException {
        double duration = Math.max(0, durationSeconds);
        double position = duration > 0
                ? Math.min(Math.max(0, positionSeconds), duration)
                : Math.max(0, positionSeconds);
        double progress = duration > 0 ? position / duration : 0.03;
        JSONObject history = history(profileId);
        String key = mediaKey(media);
        JSONObject previous = history.optJSONObject(key);
        if (previous == null) previous = new JSONObject();
        JSONObject value = new JSONObject();
        if (progress >= 0.98) {
            JsonUtils.put(value, "status", "finished");
            JsonUtils.put(value, "progress", 1.0);
            JSONObject mediaSnapshot = snapshot(media);
            if (mediaSnapshot.length() == 0) mediaSnapshot = JsonUtils.copy(previous.optJSONObject("media"));
            JsonUtils.put(value, "media", mediaSnapshot);
            if (previous.optJSONObject("episodes") != null) {
                JsonUtils.put(value, "episodes", previous.optJSONObject("episodes"));
            }
        } else {
            JsonUtils.put(value, "status", "in_progress");
            JsonUtils.put(value, "progress", Math.max(0.03, Math.min(0.97, progress)));
            JsonUtils.put(value, "media", snapshot(media));
        }
        JsonUtils.put(value, "updated_at", now());
        JsonUtils.put(value, "position_seconds", rounded(position));
        JsonUtils.put(value, "duration_seconds", rounded(duration));
        JsonUtils.put(history, key, value);
        setHistory(profileId, history);
    }

    synchronized JSONObject episodeEntry(
            String profileId,
            JSONObject media,
            int seasonNumber,
            int episodeNumber
    ) throws PiStickException {
        JSONObject show = history(profileId).optJSONObject(mediaKey(media));
        JSONObject episodes = show == null ? null : show.optJSONObject("episodes");
        return JsonUtils.copy(episodes == null ? null : episodes.optJSONObject(episodeKey(seasonNumber, episodeNumber)));
    }

    synchronized int[] resumeEpisode(String profileId, JSONObject media) throws PiStickException {
        JSONObject show = history(profileId).optJSONObject(mediaKey(media));
        JSONObject episodes = show == null ? null : show.optJSONObject("episodes");
        JSONObject latest = null;
        if (episodes != null) {
            Iterator<String> keys = episodes.keys();
            while (keys.hasNext()) {
                JSONObject candidate = episodes.optJSONObject(keys.next());
                if (candidate != null && (latest == null
                        || JsonUtils.number(candidate.opt("updated_at"), 0)
                        > JsonUtils.number(latest.opt("updated_at"), 0))) {
                    latest = candidate;
                }
            }
        }
        if (latest == null) {
            for (JSONObject season : availableSeasons(media)) {
                int number = season.optInt("season_number", 0);
                if (number > 0) return new int[]{number, 1};
            }
            return new int[]{1, 1};
        }
        int season = latest.optInt("season_number", 1);
        int episode = latest.optInt("episode_number", 1);
        if ("finished".equals(latest.optString("status"))) {
            int[] following = nextEpisodePosition(media, season, episode);
            if (following != null) return following;
        }
        return new int[]{season, episode};
    }

    synchronized void markEpisodeStarted(String profileId, JSONObject media, JSONObject episode)
            throws PiStickException {
        int season = episode.optInt("season_number", 1);
        int number = episode.optInt("episode_number", 1);
        JSONObject previous = episodeEntry(profileId, media, season, number);
        double progress = JsonUtils.number(previous.opt("progress"), 0);
        if ("finished".equals(previous.optString("status"))) progress = 0.03;
        setEpisodeProgress(profileId, media, episode, Math.max(0.03, progress), null, null);
    }

    synchronized void markEpisodeFinished(String profileId, JSONObject media, JSONObject episode)
            throws PiStickException {
        setEpisodeProgress(profileId, media, episode, 1.0, null, null);
    }

    synchronized void setEpisodePosition(
            String profileId,
            JSONObject media,
            JSONObject episode,
            double positionSeconds,
            double durationSeconds
    ) throws PiStickException {
        double duration = Math.max(0, durationSeconds);
        double position = duration > 0
                ? Math.min(Math.max(0, positionSeconds), duration)
                : Math.max(0, positionSeconds);
        setEpisodeProgress(
                profileId,
                media,
                episode,
                duration > 0 ? position / duration : 0.03,
                position,
                duration
        );
    }

    synchronized JSONArray continueWatching(String profileId) throws PiStickException {
        List<JSONObject> values = new ArrayList<>();
        JSONObject history = history(profileId);
        Iterator<String> keys = history.keys();
        while (keys.hasNext()) {
            JSONObject value = history.optJSONObject(keys.next());
            if (value != null && "in_progress".equals(value.optString("status"))
                    && value.optJSONObject("media") != null) {
                values.add(value);
            }
        }
        values.sort((first, second) -> Double.compare(
                JsonUtils.number(second.opt("updated_at"), 0),
                JsonUtils.number(first.opt("updated_at"), 0)
        ));
        JSONArray result = new JSONArray();
        for (JSONObject value : values) {
            JSONObject media = JsonUtils.copy(value.optJSONObject("media"));
            JsonUtils.put(media, "watch", JsonUtils.selected(value,
                    "status", "progress", "position_seconds", "duration_seconds", "updated_at", "last_episode"));
            result.put(media);
        }
        return result;
    }

    synchronized List<JSONObject> availableSeasons(JSONObject media) {
        List<JSONObject> regular = new ArrayList<>();
        List<JSONObject> specials = new ArrayList<>();
        JSONArray seasons = media == null ? null : media.optJSONArray("seasons");
        if (seasons != null) {
            for (int index = 0; index < seasons.length(); index++) {
                JSONObject season = seasons.optJSONObject(index);
                if (season == null || season.optInt("episode_count", 0) <= 0) continue;
                if (season.optInt("season_number", 0) > 0) regular.add(season);
                else specials.add(season);
            }
        }
        regular.sort(Comparator.comparingInt(item -> item.optInt("season_number", 0)));
        specials.sort(Comparator.comparing(item -> item.optString("name", "")));
        regular.addAll(specials);
        return regular;
    }

    private void setEpisodeProgress(
            String profileId,
            JSONObject media,
            JSONObject episode,
            double progress,
            Double position,
            Double duration
    ) throws PiStickException {
        if (!"tv".equals(media.optString("media_type"))) {
            throw new PiStickException("Episode progress requires a TV show.");
        }
        int season = episode.optInt("season_number", 1);
        int number = episode.optInt("episode_number", 1);
        if (season < 0 || number <= 0) throw new PiStickException("Episode numbers are invalid.");
        double bounded = Math.max(0, Math.min(1, progress));
        double timestamp = now();
        JSONObject history = history(profileId);
        String key = mediaKey(media);
        JSONObject previousShow = history.optJSONObject(key);
        if (previousShow == null) previousShow = new JSONObject();
        JSONObject episodes = JsonUtils.copy(previousShow.optJSONObject("episodes"));

        JSONObject episodeValue = new JSONObject();
        JsonUtils.put(episodeValue, "status", bounded >= 0.98 ? "finished" : "in_progress");
        JsonUtils.put(episodeValue, "progress", bounded >= 0.98 ? 1.0 : Math.max(0.03, bounded));
        JsonUtils.put(episodeValue, "updated_at", timestamp);
        JsonUtils.put(episodeValue, "season_number", season);
        JsonUtils.put(episodeValue, "episode_number", number);
        JsonUtils.put(episodeValue, "episode", episodeSnapshot(episode));
        if (position != null) JsonUtils.put(episodeValue, "position_seconds", rounded(Math.max(0, position)));
        if (duration != null) JsonUtils.put(episodeValue, "duration_seconds", rounded(Math.max(0, duration)));
        JsonUtils.put(episodes, episodeKey(season, number), episodeValue);

        int[] following = nextEpisodePosition(media, season, number);
        boolean episodeFinished = "finished".equals(episodeValue.optString("status"));
        boolean showFinished = episodeFinished && following == null;
        JSONObject showValue = new JSONObject();
        JsonUtils.put(showValue, "status", showFinished ? "finished" : "in_progress");
        JsonUtils.put(showValue, "progress", showFinished
                ? 1.0
                : (episodeFinished ? 0.03 : Math.max(0.03, Math.min(0.97, episodeValue.optDouble("progress", 0.03)))));
        JsonUtils.put(showValue, "updated_at", timestamp);
        JSONObject mediaSnapshot = snapshot(media);
        if (mediaSnapshot.length() == 0) mediaSnapshot = JsonUtils.copy(previousShow.optJSONObject("media"));
        JsonUtils.put(showValue, "media", mediaSnapshot);
        JsonUtils.put(showValue, "episodes", episodes);
        JSONObject lastEpisode = new JSONObject();
        JsonUtils.put(lastEpisode, "season_number", season);
        JsonUtils.put(lastEpisode, "episode_number", number);
        JsonUtils.put(showValue, "last_episode", lastEpisode);
        JsonUtils.put(history, key, showValue);
        setHistory(profileId, history);
    }

    private int[] nextEpisodePosition(JSONObject media, int seasonNumber, int episodeNumber) {
        List<JSONObject> seasons = availableSeasons(media);
        for (int index = 0; index < seasons.size(); index++) {
            JSONObject season = seasons.get(index);
            int number = season.optInt("season_number", 0);
            int count = season.optInt("episode_count", 0);
            if (number == seasonNumber) {
                if (count > episodeNumber) return new int[]{seasonNumber, episodeNumber + 1};
                for (int next = index + 1; next < seasons.size(); next++) {
                    JSONObject nextSeason = seasons.get(next);
                    int nextNumber = nextSeason.optInt("season_number", 0);
                    if (nextNumber > 0 && nextSeason.optInt("episode_count", 0) > 0) {
                        return new int[]{nextNumber, 1};
                    }
                }
                return null;
            }
        }
        return null;
    }

    private JSONObject watchState() {
        JSONObject watch = data.optJSONObject("watch_state");
        if (watch == null) {
            watch = new JSONObject();
            JsonUtils.put(data, "watch_state", watch);
        }
        return watch;
    }

    private JSONObject history(String profileId) throws PiStickException {
        requireProfile(profileId);
        JSONObject watch = watchState();
        JSONObject history = watch.optJSONObject(profileId);
        if (history == null) {
            history = new JSONObject();
            JsonUtils.put(watch, profileId, history);
        }
        return history;
    }

    private void setHistory(String profileId, JSONObject history) throws PiStickException {
        JsonUtils.put(watchState(), profileId, history);
        save();
    }

    private JSONObject requireProfile(String id) throws PiStickException {
        JSONObject found = profile(id);
        if (found == null) throw new PiStickException("Choose a valid profile first.");
        return found;
    }

    private String mediaKey(JSONObject media) throws PiStickException {
        String kind = media == null
                ? ""
                : media.optString("media_type", "").toLowerCase(Locale.ROOT);
        int id = media == null ? 0 : media.optInt("id", 0);
        if (!("movie".equals(kind) || "tv".equals(kind)) || id <= 0) {
            throw new PiStickException("Media must contain a valid TMDB ID and type.");
        }
        return kind + ":" + id;
    }

    private JSONObject snapshot(JSONObject media) {
        return JsonUtils.selected(media,
                "id", "media_type", "title", "name", "year", "release_date", "first_air_date",
                "poster_path", "backdrop_path", "overview", "vote_average", "number_of_seasons", "seasons");
    }

    private JSONObject episodeSnapshot(JSONObject episode) {
        return JsonUtils.selected(episode,
                "id", "name", "overview", "air_date", "still_path", "runtime", "season_number", "episode_number");
    }

    private void normalize() {
        JSONArray sourceProfiles = data.optJSONArray("profiles");
        JSONArray normalizedProfiles = new JSONArray();
        Set<String> seen = new HashSet<>();
        if (sourceProfiles != null) {
            for (int index = 0; index < sourceProfiles.length() && normalizedProfiles.length() < MAXIMUM_PROFILES; index++) {
                JSONObject candidate = sourceProfiles.optJSONObject(index);
                if (candidate == null) continue;
                String id = candidate.optString("id", "").trim();
                if (id.isEmpty() || seen.contains(id)) {
                    id = "profile-" + UUID.randomUUID().toString().replace("-", "").substring(0, 10);
                }
                seen.add(id);
                String name = cleanName(candidate.optString("name", ""), "Profile " + (normalizedProfiles.length() + 1));
                String avatar = candidate.optString("avatar", "").toLowerCase(Locale.ROOT);
                if (!validAvatar(avatar)) avatar = AVATARS[normalizedProfiles.length() % AVATARS.length];
                JSONObject profile = new JSONObject();
                JsonUtils.put(profile, "id", id);
                JsonUtils.put(profile, "name", name);
                JsonUtils.put(profile, "avatar", avatar);
                normalizedProfiles.put(profile);
            }
        }
        if (normalizedProfiles.length() == 0) normalizedProfiles = defaultProfiles();

        JSONObject oldWatch = data.optJSONObject("watch_state");
        JSONObject normalizedWatch = new JSONObject();
        for (int index = 0; index < normalizedProfiles.length(); index++) {
            JSONObject profile = normalizedProfiles.optJSONObject(index);
            String id = profile == null ? "" : profile.optString("id");
            JSONObject history = oldWatch == null ? null : oldWatch.optJSONObject(id);
            JsonUtils.put(normalizedWatch, id, history == null ? new JSONObject() : history);
        }
        String active = JsonUtils.string(data.opt("active_profile"));
        boolean activeExists = false;
        for (int index = 0; index < normalizedProfiles.length(); index++) {
            JSONObject profile = normalizedProfiles.optJSONObject(index);
            if (profile != null && active.equals(profile.optString("id"))) activeExists = true;
        }
        JSONObject normalized = new JSONObject();
        JsonUtils.put(normalized, "active_profile", activeExists ? active : JSONObject.NULL);
        JsonUtils.put(normalized, "profiles", normalizedProfiles);
        JsonUtils.put(normalized, "watch_state", normalizedWatch);
        data = normalized;
    }

    private void save() throws PiStickException {
        File directory = file.getParentFile();
        if (directory != null && !directory.exists() && !directory.mkdirs()) {
            throw new PiStickException("PiStick could not create its private storage folder.");
        }
        File temporary = new File(file.getPath() + ".new");
        byte[] bytes = data.toString().getBytes(StandardCharsets.UTF_8);
        try (FileOutputStream output = new FileOutputStream(temporary)) {
            output.write(bytes);
            output.flush();
            output.getFD().sync();
        } catch (IOException error) {
            throw new PiStickException("PiStick could not save watch progress.", error);
        }
        try {
            try {
                Files.move(
                        temporary.toPath(),
                        file.toPath(),
                        StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING
                );
            } catch (AtomicMoveNotSupportedException ignored) {
                Files.move(temporary.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (IOException error) {
            //noinspection ResultOfMethodCallIgnored
            temporary.delete();
            throw new PiStickException("PiStick could not finish saving watch progress.", error);
        }
    }

    private static JSONObject read(File file) {
        if (!file.isFile()) return defaultData();
        try {
            String content = new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8);
            return new JSONObject(content);
        } catch (IOException | JSONException ignored) {
            return defaultData();
        }
    }

    private static JSONObject defaultData() {
        JSONObject data = new JSONObject();
        JSONArray profiles = defaultProfiles();
        JSONObject watch = new JSONObject();
        JsonUtils.put(watch, "profile-1", new JSONObject());
        JsonUtils.put(data, "active_profile", JSONObject.NULL);
        JsonUtils.put(data, "profiles", profiles);
        JsonUtils.put(data, "watch_state", watch);
        return data;
    }

    private static JSONArray defaultProfiles() {
        JSONObject profile = new JSONObject();
        JsonUtils.put(profile, "id", "profile-1");
        JsonUtils.put(profile, "name", "Profile 1");
        JsonUtils.put(profile, "avatar", "red");
        return new JSONArray().put(profile);
    }

    private static String cleanName(String name, String fallback) {
        String cleaned = truncate(name == null ? "" : name.trim(), 40);
        return cleaned.isEmpty() ? fallback : cleaned;
    }

    private static String truncate(String value, int maximumCodePoints) {
        int count = value.codePointCount(0, value.length());
        if (count <= maximumCodePoints) return value;
        return value.substring(0, value.offsetByCodePoints(0, maximumCodePoints));
    }

    private static boolean validAvatar(String candidate) {
        for (String avatar : AVATARS) if (avatar.equals(candidate)) return true;
        return false;
    }

    private static String episodeKey(int season, int episode) {
        return season + ":" + episode;
    }

    private static double now() {
        return System.currentTimeMillis() / 1000.0;
    }

    private static double rounded(double value) {
        return Math.round(value * 10.0) / 10.0;
    }

    private static void copyIfPresent(JSONObject source, JSONObject target, String... keys) {
        for (String key : keys) {
            Object value = source.opt(key);
            if (!JsonUtils.isNull(value)) JsonUtils.put(target, key, value);
        }
    }
}
