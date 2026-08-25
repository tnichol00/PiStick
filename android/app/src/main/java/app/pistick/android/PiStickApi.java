package app.pistick.android;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.UnsupportedEncodingException;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.URLDecoder;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

final class PiStickApi implements AutoCloseable {
    private final CredentialStore credentials;
    private final StateStore state;
    private final TmdbClient tmdb;

    PiStickApi(Context context) {
        credentials = new CredentialStore(context);
        state = new StateStore(context);
        tmdb = new TmdbClient(credentials::credential);
    }

    JSONObject handle(String rawPath, String rawMethod, JSONObject body) throws PiStickException {
        RequestTarget target = RequestTarget.parse(rawPath);
        List<String> parts = target.parts;
        Map<String, String> query = target.query;
        String method = rawMethod == null || rawMethod.trim().isEmpty()
                ? "GET"
                : rawMethod.toUpperCase(Locale.ROOT);
        JSONObject requestBody = body == null ? new JSONObject() : body;

        if (matches(parts, "api", "status") && "GET".equals(method)) {
            JSONObject result = new JSONObject();
            JsonUtils.put(result, "name", "PiStick");
            JsonUtils.put(result, "version", BuildConfig.VERSION_NAME);
            JsonUtils.put(result, "tmdb_configured", credentials.isConfigured());
            return result;
        }

        if (matches(parts, "api", "profiles") && "GET".equals(method)) {
            return state.profilesPayload();
        }
        if (matches(parts, "api", "profiles") && "POST".equals(method)) {
            return JsonUtils.put(new JSONObject(), "profile", state.addProfile(requestBody.optString("name", "")));
        }
        if (parts.size() == 3 && "api".equals(parts.get(0)) && "profiles".equals(parts.get(1))
                && "PATCH".equals(method)) {
            return JsonUtils.put(new JSONObject(), "profile",
                    state.renameProfile(parts.get(2), requestBody.optString("name", "")));
        }
        if (parts.size() == 3 && "api".equals(parts.get(0)) && "profiles".equals(parts.get(1))
                && "DELETE".equals(method)) {
            state.deleteProfile(parts.get(2));
            return JsonUtils.put(new JSONObject(), "ok", true);
        }
        if (parts.size() == 4 && "api".equals(parts.get(0)) && "profiles".equals(parts.get(1))
                && "activate".equals(parts.get(3)) && "POST".equals(method)) {
            return JsonUtils.put(new JSONObject(), "profile", state.activateProfile(parts.get(2)));
        }

        if (matches(parts, "api", "settings", "tmdb") && "POST".equals(method)) {
            String credential = requestBody.optString("token", "");
            if (!CredentialStore.isUsable(credential)) {
                throw new PiStickException("Paste your TMDB API Read Access Token or v3 API key.");
            }
            tmdb.validate(credential);
            credentials.save(credential);
            tmdb.clearCache();
            JSONObject result = new JSONObject();
            JsonUtils.put(result, "ok", true);
            JsonUtils.put(result, "tmdb_configured", true);
            return result;
        }

        if (matches(parts, "api", "home") && "GET".equals(method)) {
            String profileId = requiredProfileId(query.get("profile_id"));
            return decorateHome(profileId, tmdb.home());
        }
        if (parts.size() == 3 && "api".equals(parts.get(0)) && "discover".equals(parts.get(1))
                && ("movie".equals(parts.get(2)) || "tv".equals(parts.get(2))) && "GET".equals(method)) {
            String profileId = requiredProfileId(query.get("profile_id"));
            int page = integer(query.getOrDefault("page", "1"), "Page", 1, 500);
            JSONObject payload = tmdb.discover(parts.get(2), page);
            JsonUtils.put(payload, "items", decorateItems(profileId, payload.optJSONArray("items")));
            return payload;
        }
        if (matches(parts, "api", "search") && "GET".equals(method)) {
            String profileId = requiredProfileId(query.get("profile_id"));
            int page = integer(query.getOrDefault("page", "1"), "Page", 1, 500);
            JSONObject payload = tmdb.search(query.getOrDefault("q", ""), page);
            JsonUtils.put(payload, "items", decorateItems(profileId, payload.optJSONArray("items")));
            return payload;
        }

        if (parts.size() == 4 && "api".equals(parts.get(0)) && "media".equals(parts.get(1))
                && ("movie".equals(parts.get(2)) || "tv".equals(parts.get(2))) && "GET".equals(method)) {
            String profileId = requiredProfileId(query.get("profile_id"));
            int id = integer(parts.get(3), "TMDB ID", 1, Integer.MAX_VALUE);
            JSONObject media = tmdb.details(parts.get(2), id);
            JSONObject decorated = state.decorate(profileId, media);
            if ("tv".equals(parts.get(2))) {
                int[] resume = state.resumeEpisode(profileId, media);
                JSONObject resumeJson = new JSONObject();
                JsonUtils.put(resumeJson, "season_number", resume[0]);
                JsonUtils.put(resumeJson, "episode_number", resume[1]);
                JsonUtils.put(decorated, "resume_episode", resumeJson);
            }
            return JsonUtils.put(new JSONObject(), "media", decorated);
        }

        if (parts.size() == 5 && "api".equals(parts.get(0)) && "tv".equals(parts.get(1))
                && "season".equals(parts.get(3)) && "GET".equals(method)) {
            String profileId = requiredProfileId(query.get("profile_id"));
            int showId = integer(parts.get(2), "TMDB ID", 1, Integer.MAX_VALUE);
            int seasonNumber = integer(parts.get(4), "Season", 0, 10_000);
            return seasonPayload(showId, seasonNumber, profileId);
        }

        if (matches(parts, "api", "continue") && "GET".equals(method)) {
            return JsonUtils.put(new JSONObject(), "items",
                    state.continueWatching(requiredProfileId(query.get("profile_id"))));
        }

        if (matches(parts, "api", "play") && "POST".equals(method)) {
            String profileId = requiredProfileId(requestBody.optString("profile_id", ""));
            JSONObject media = requiredMedia(requestBody.opt("media"));
            String kind = media.optString("media_type", "");
            int mediaId = integer(media.opt("id"), "TMDB ID", 1, Integer.MAX_VALUE);
            if ("movie".equals(kind)) {
                JSONObject saved = state.entry(profileId, media);
                double resume = JsonUtils.number(saved.opt("position_seconds"), 0);
                state.markStarted(profileId, media);
                JSONObject result = new JSONObject();
                JsonUtils.put(result, "embed_url", playbackUrl("/movie/" + mediaId, resume));
                JsonUtils.put(result, "resume_seconds", resume);
                JsonUtils.put(result, "kind", "movie");
                return result;
            }
            JSONObject episode = requestBody.optJSONObject("episode");
            if (episode == null) throw new PiStickException("Choose an episode first.");
            int seasonNumber = integer(episode.opt("season_number"), "Season", 0, 10_000);
            int episodeNumber = integer(episode.opt("episode_number"), "Episode", 1, 100_000);
            JSONObject saved = state.episodeEntry(profileId, media, seasonNumber, episodeNumber);
            double resume = JsonUtils.number(saved.opt("position_seconds"), 0);
            state.markEpisodeStarted(profileId, media, episode);
            JSONObject result = new JSONObject();
            JsonUtils.put(result, "embed_url",
                    playbackUrl("/tv/" + mediaId + "/" + seasonNumber + "/" + episodeNumber, resume));
            JsonUtils.put(result, "resume_seconds", resume);
            JsonUtils.put(result, "kind", "episode");
            JsonUtils.put(result, "season_number", seasonNumber);
            JsonUtils.put(result, "episode_number", episodeNumber);
            return result;
        }

        if (matches(parts, "api", "watch", "progress") && "POST".equals(method)) {
            String profileId = requiredProfileId(requestBody.optString("profile_id", ""));
            JSONObject media = requiredMedia(requestBody.opt("media"));
            double position = number(requestBody.opt("position_seconds"), "Position");
            double duration = number(requestBody.opt("duration_seconds"), "Duration");
            JSONObject episode = requestBody.optJSONObject("episode");
            if (episode == null) state.setPosition(profileId, media, position, duration);
            else state.setEpisodePosition(profileId, media, episode, position, duration);
            return JsonUtils.put(new JSONObject(), "ok", true);
        }

        if (matches(parts, "api", "watch", "action") && "POST".equals(method)) {
            String profileId = requiredProfileId(requestBody.optString("profile_id", ""));
            JSONObject media = requiredMedia(requestBody.opt("media"));
            switch (requestBody.optString("action", "")) {
                case "finished":
                case "show_finished":
                    state.markFinished(profileId, media);
                    break;
                case "unwatched":
                    state.markUnwatched(profileId, media);
                    break;
                case "started":
                    state.markStarted(profileId, media);
                    break;
                case "episode_finished":
                    JSONObject episode = requestBody.optJSONObject("episode");
                    if (episode == null) throw new PiStickException("Episode data is invalid.");
                    state.markEpisodeFinished(profileId, media, episode);
                    break;
                default:
                    throw new PiStickException("Watch-state action is invalid.");
            }
            return JsonUtils.put(new JSONObject(), "ok", true);
        }

        throw new PiStickException("App route not found.");
    }

    @Override
    public void close() {
        tmdb.close();
    }

    private JSONObject decorateHome(String profileId, JSONObject payload) throws PiStickException {
        JSONArray rows = new JSONArray();
        JSONArray continuing = state.continueWatching(profileId);
        if (continuing.length() > 0) {
            JSONObject row = new JSONObject();
            JsonUtils.put(row, "title", "Continue Watching");
            JsonUtils.put(row, "items", continuing);
            rows.put(row);
        }
        JSONArray sourceRows = payload.optJSONArray("rows");
        if (sourceRows != null) {
            for (int index = 0; index < sourceRows.length(); index++) {
                JSONObject source = sourceRows.optJSONObject(index);
                if (source == null) continue;
                JSONObject row = new JSONObject();
                String title = source.optString("title", "");
                JsonUtils.put(row, "title", title.isEmpty() ? "Explore" : title);
                JsonUtils.put(row, "items", decorateItems(profileId, source.optJSONArray("items")));
                rows.put(row);
            }
        }
        Object rawHero = payload.opt("hero");
        Object hero = JSONObject.NULL;
        if (rawHero instanceof JSONObject) hero = state.decorate(profileId, (JSONObject) rawHero);
        JSONObject result = new JSONObject();
        JsonUtils.put(result, "hero", hero);
        JsonUtils.put(result, "rows", rows);
        return result;
    }

    private JSONObject seasonPayload(int showId, int seasonNumber, String profileId) throws PiStickException {
        JSONObject show = tmdb.details("tv", showId);
        JSONObject season = tmdb.season(showId, seasonNumber);
        JSONArray decorated = new JSONArray();
        JSONArray episodes = season.optJSONArray("episodes");
        if (episodes != null) {
            for (int index = 0; index < episodes.length(); index++) {
                JSONObject episode = JsonUtils.copy(episodes.optJSONObject(index));
                int selectedSeason = episode.optInt("season_number", seasonNumber);
                int selectedEpisode = episode.optInt("episode_number", 0);
                JSONObject watch = state.episodeEntry(profileId, show, selectedSeason, selectedEpisode);
                if (watch.length() > 0) {
                    JsonUtils.put(episode, "watch", JsonUtils.selected(watch,
                            "status", "progress", "position_seconds", "duration_seconds"));
                }
                decorated.put(episode);
            }
        }
        JsonUtils.put(season, "episodes", decorated);
        return JsonUtils.put(new JSONObject(), "season", season);
    }

    private JSONArray decorateItems(String profileId, JSONArray items) throws PiStickException {
        JSONArray result = new JSONArray();
        if (items == null) return result;
        for (int index = 0; index < items.length(); index++) {
            JSONObject item = items.optJSONObject(index);
            if (item != null) result.put(state.decorate(profileId, item));
        }
        return result;
    }

    private String requiredProfileId(String candidate) throws PiStickException {
        String requested = candidate == null ? "" : candidate.trim();
        String selected = requested.isEmpty() ? state.activeProfileId() : requested;
        if (selected == null || state.profile(selected) == null) {
            throw new PiStickException("Choose a profile first.");
        }
        return selected;
    }

    private JSONObject requiredMedia(Object candidate) throws PiStickException {
        JSONObject media = candidate instanceof JSONObject ? (JSONObject) candidate : null;
        if (media == null) throw new PiStickException("A media object is required.");
        String kind = media.optString("media_type", "");
        if (!("movie".equals(kind) || "tv".equals(kind)) || media.optInt("id", 0) <= 0) {
            throw new PiStickException("Media must contain a valid TMDB ID and type.");
        }
        return media;
    }

    private int integer(Object candidate, String name, int minimum, int maximum) throws PiStickException {
        double parsed = JsonUtils.number(candidate, Double.NaN);
        if (!Double.isFinite(parsed) || Math.rint(parsed) != parsed || parsed < minimum || parsed > maximum) {
            throw new PiStickException(name + " is outside the allowed range.");
        }
        return (int) parsed;
    }

    private double number(Object candidate, String name) throws PiStickException {
        double value = JsonUtils.number(candidate, Double.NaN);
        if (!Double.isFinite(value) || value < 0 || value > 10_000_000) {
            throw new PiStickException(name + " must be a non-negative number.");
        }
        return value;
    }

    static String playbackUrl(String path, double resumeSeconds) {
        int seconds = Math.max(0, (int) resumeSeconds);
        StringBuilder url = new StringBuilder("https://player.videasy.to").append(path);
        url.append("?autoplay=true");
        if (path.startsWith("/tv/")) url.append("&autoplayNextEpisode=true");
        if (seconds > 0) url.append("&progress=").append(seconds);
        return url.toString();
    }

    private static boolean matches(List<String> parts, String... expected) {
        if (parts.size() != expected.length) return false;
        for (int index = 0; index < expected.length; index++) {
            if (!expected[index].equals(parts.get(index))) return false;
        }
        return true;
    }

    private static final class RequestTarget {
        final List<String> parts;
        final Map<String, String> query;

        RequestTarget(List<String> parts, Map<String, String> query) {
            this.parts = parts;
            this.query = query;
        }

        static RequestTarget parse(String rawPath) throws PiStickException {
            if (rawPath == null || rawPath.length() > 4096 || !rawPath.startsWith("/api/")) {
                throw new PiStickException("The app request is invalid.");
            }
            try {
                URI uri = new URI(rawPath);
                List<String> parts = new ArrayList<>();
                for (String piece : uri.getRawPath().split("/")) {
                    if (!piece.isEmpty()) parts.add(decode(piece));
                }
                Map<String, String> query = new LinkedHashMap<>();
                String rawQuery = uri.getRawQuery();
                if (rawQuery != null && !rawQuery.isEmpty()) {
                    for (String pair : rawQuery.split("&")) {
                        String[] values = pair.split("=", 2);
                        String key = decode(values[0]);
                        if (!query.containsKey(key)) {
                            query.put(key, values.length == 2 ? decode(values[1]) : "");
                        }
                    }
                }
                return new RequestTarget(parts, query);
            } catch (URISyntaxException | IllegalArgumentException error) {
                throw new PiStickException("The app request is invalid.", error);
            }
        }

        private static String decode(String value) {
            try {
                return URLDecoder.decode(value, "UTF-8");
            } catch (UnsupportedEncodingException error) {
                throw new IllegalStateException(error);
            }
        }
    }
}
