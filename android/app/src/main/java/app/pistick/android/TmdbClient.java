package app.pistick.android;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.io.UnsupportedEncodingException;
import java.net.URLEncoder;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import javax.net.ssl.HttpsURLConnection;

final class TmdbClient implements AutoCloseable {
    private static final String API_BASE = "https://api.themoviedb.org/3";
    private static final long LIST_CACHE_MILLIS = 5 * 60 * 1000L;
    private static final long DETAIL_CACHE_MILLIS = 30 * 60 * 1000L;
    private static final int MAX_RESPONSE_BYTES = 10 * 1024 * 1024;
    private static final int MAX_CACHE_ENTRIES = 64;

    interface CredentialProvider {
        String credential();
    }

    private static final class CacheEntry {
        final long expiresAt;
        final String payload;

        CacheEntry(long expiresAt, String payload) {
            this.expiresAt = expiresAt;
            this.payload = payload;
        }
    }

    private static final class RowDefinition {
        final String title;
        final String endpoint;
        final String mediaType;

        RowDefinition(String title, String endpoint, String mediaType) {
            this.title = title;
            this.endpoint = endpoint;
            this.mediaType = mediaType;
        }
    }

    private static final class RowResult {
        final JSONObject row;
        final PiStickException error;

        RowResult(JSONObject row, PiStickException error) {
            this.row = row;
            this.error = error;
        }
    }

    private final CredentialProvider credentialProvider;
    private final ExecutorService networkExecutor = Executors.newFixedThreadPool(5, runnable -> {
        Thread thread = new Thread(runnable, "PiStick-TMDB");
        thread.setPriority(Thread.NORM_PRIORITY - 1);
        return thread;
    });
    private final Map<String, CacheEntry> cache = Collections.synchronizedMap(
            new LinkedHashMap<String, CacheEntry>(MAX_CACHE_ENTRIES + 1, 0.75f, true) {
                @Override
                protected boolean removeEldestEntry(Map.Entry<String, CacheEntry> eldest) {
                    return size() > MAX_CACHE_ENTRIES;
                }
            }
    );

    TmdbClient(CredentialProvider credentialProvider) {
        this.credentialProvider = credentialProvider;
    }

    void validate(String candidate) throws PiStickException {
        request("/configuration", Collections.emptyMap(), candidate, 0);
    }

    JSONObject home() throws PiStickException {
        List<RowDefinition> definitions = Arrays.asList(
                new RowDefinition("Trending Now", "/trending/all/week", null),
                new RowDefinition("Popular Movies", "/movie/popular", "movie"),
                new RowDefinition("Top Rated Movies", "/movie/top_rated", "movie"),
                new RowDefinition("Popular TV Shows", "/tv/popular", "tv"),
                new RowDefinition("Top Rated TV", "/tv/top_rated", "tv")
        );
        List<Future<RowResult>> futures = new ArrayList<>(definitions.size());
        for (RowDefinition definition : definitions) {
            futures.add(networkExecutor.submit(() -> {
                try {
                    JSONObject payload = request(
                            definition.endpoint,
                            parameters("page", "1"),
                            null,
                            LIST_CACHE_MILLIS
                    );
                    JSONObject row = new JSONObject();
                    JsonUtils.put(row, "title", definition.title);
                    JsonUtils.put(row, "items", items(payload, definition.mediaType));
                    return new RowResult(row, null);
                } catch (PiStickException error) {
                    JSONObject row = new JSONObject();
                    JsonUtils.put(row, "title", definition.title);
                    JsonUtils.put(row, "items", new JSONArray());
                    return new RowResult(row, error);
                }
            }));
        }

        JSONArray rows = new JSONArray();
        PiStickException firstError = null;
        JSONObject hero = null;
        for (Future<RowResult> future : futures) {
            RowResult result;
            try {
                result = future.get();
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                throw new PiStickException("The TMDB request was interrupted.", error);
            } catch (ExecutionException error) {
                throw new PiStickException("PiStick could not load titles from TMDB.", error);
            }
            rows.put(result.row);
            if (firstError == null) firstError = result.error;
            if (hero == null) {
                JSONArray candidates = result.row.optJSONArray("items");
                if (candidates != null) {
                    for (int index = 0; index < candidates.length(); index++) {
                        JSONObject candidate = candidates.optJSONObject(index);
                        if (candidate != null && !candidate.optString("backdrop_path", "").isEmpty()) {
                            hero = candidate;
                            break;
                        }
                    }
                }
            }
        }
        if (hero == null && firstError != null) throw firstError;
        JSONObject result = new JSONObject();
        JsonUtils.put(result, "hero", hero == null ? JSONObject.NULL : hero);
        JsonUtils.put(result, "rows", rows);
        return result;
    }

    JSONObject discover(String mediaType, int page) throws PiStickException {
        if (!("movie".equals(mediaType) || "tv".equals(mediaType))) {
            throw new PiStickException("Discover type must be movie or tv.");
        }
        int selectedPage = Math.max(1, Math.min(page, 500));
        JSONObject payload = request(
                "/" + mediaType + "/popular",
                parameters("page", String.valueOf(selectedPage)),
                null,
                LIST_CACHE_MILLIS
        );
        JSONObject result = new JSONObject();
        JsonUtils.put(result, "page", payload.optInt("page", selectedPage));
        JsonUtils.put(result, "total_pages", payload.optInt("total_pages", 1));
        JsonUtils.put(result, "items", items(payload, mediaType));
        return result;
    }

    JSONObject search(String query, int page) throws PiStickException {
        String cleaned = truncate(query == null ? "" : query.trim(), 120);
        if (cleaned.isEmpty()) {
            JSONObject empty = new JSONObject();
            JsonUtils.put(empty, "page", 1);
            JsonUtils.put(empty, "total_pages", 1);
            JsonUtils.put(empty, "items", new JSONArray());
            return empty;
        }
        int selectedPage = Math.max(1, Math.min(page, 500));
        JSONObject payload = request(
                "/search/multi",
                parameters(
                        "query", cleaned,
                        "page", String.valueOf(selectedPage),
                        "include_adult", "false"
                ),
                null,
                2 * 60 * 1000L
        );
        JSONObject result = new JSONObject();
        JsonUtils.put(result, "page", payload.optInt("page", selectedPage));
        JsonUtils.put(result, "total_pages", payload.optInt("total_pages", 1));
        JsonUtils.put(result, "items", items(payload, null));
        return result;
    }

    JSONObject details(String mediaType, int id) throws PiStickException {
        if (!("movie".equals(mediaType) || "tv".equals(mediaType)) || id <= 0) {
            throw new PiStickException("The TMDB title is invalid.");
        }
        JSONObject payload = request(
                "/" + mediaType + "/" + id,
                parameters("append_to_response", "videos"),
                null,
                DETAIL_CACHE_MILLIS
        );
        return normalize(payload, mediaType);
    }

    JSONObject season(int showId, int seasonNumber) throws PiStickException {
        if (showId <= 0 || seasonNumber < 0) {
            throw new PiStickException("The show or season number is invalid.");
        }
        JSONObject payload = request(
                "/tv/" + showId + "/season/" + seasonNumber,
                Collections.emptyMap(),
                null,
                DETAIL_CACHE_MILLIS
        );
        JSONObject result = JsonUtils.copy(payload);
        JSONArray episodes = new JSONArray();
        JSONArray candidates = payload.optJSONArray("episodes");
        if (candidates != null) {
            for (int index = 0; index < candidates.length(); index++) {
                JSONObject episode = JsonUtils.selected(candidates.optJSONObject(index),
                        "id", "name", "overview", "air_date", "still_path", "runtime",
                        "season_number", "episode_number");
                int number = episode.optInt("episode_number", 0);
                if (number <= 0) continue;
                JsonUtils.put(episode, "season_number", episode.optInt("season_number", seasonNumber));
                JsonUtils.put(episode, "episode_number", number);
                episodes.put(episode);
            }
        }
        JsonUtils.put(result, "season_number", payload.optInt("season_number", seasonNumber));
        JsonUtils.put(result, "episodes", episodes);
        return result;
    }

    synchronized void clearCache() {
        cache.clear();
    }

    @Override
    public void close() {
        networkExecutor.shutdownNow();
        clearCache();
    }

    private JSONArray items(JSONObject payload, String mediaType) {
        JSONArray result = new JSONArray();
        JSONArray candidates = payload.optJSONArray("results");
        if (candidates == null) return result;
        for (int index = 0; index < candidates.length(); index++) {
            JSONObject item = normalize(candidates.optJSONObject(index), mediaType);
            String kind = item.optString("media_type", "");
            if (("movie".equals(kind) || "tv".equals(kind)) && item.optInt("id", 0) > 0) {
                result.put(listItem(item));
            }
        }
        return result;
    }

    private JSONObject normalize(JSONObject candidate, String mediaType) {
        JSONObject result = JsonUtils.copy(candidate);
        String kind = mediaType == null
                ? result.optString("media_type", "").toLowerCase(Locale.ROOT)
                : mediaType;
        if (!("movie".equals(kind) || "tv".equals(kind))) {
            if (result.has("title")) kind = "movie";
            else if (result.has("name")) kind = "tv";
        }
        JsonUtils.put(result, "media_type", kind);
        String date = result.optString("release_date", "");
        if (date.isEmpty()) date = result.optString("first_air_date", "");
        JsonUtils.put(result, "year", date.length() >= 4 ? date.substring(0, 4) : "");
        return result;
    }

    private JSONObject listItem(JSONObject item) {
        return JsonUtils.selected(item,
                "id", "media_type", "title", "name", "year", "release_date", "first_air_date",
                "poster_path", "backdrop_path", "overview", "vote_average");
    }

    private JSONObject request(
            String endpoint,
            Map<String, String> params,
            String credentialOverride,
            long cacheMillis
    ) throws PiStickException {
        String credential = (credentialOverride == null ? credentialProvider.credential() : credentialOverride).trim();
        if (!CredentialStore.isUsable(credential)) {
            throw new PiStickException("Add your TMDB API credential in Settings.");
        }
        boolean bearer = credential.startsWith("eyJ") || credential.length() > 64;
        Map<String, String> query = new LinkedHashMap<>(params);
        query.put("language", "en-CA");
        if (!bearer) query.put("api_key", credential);

        String cacheKey = endpoint + "|" + cacheSafeQuery(params) + "|" + credential.hashCode();
        if (cacheMillis > 0) {
            JSONObject cached = cached(cacheKey);
            if (cached != null) return cached;
        }

        HttpsURLConnection connection = null;
        try {
            URL url = new URL(API_BASE + endpoint + "?" + queryString(query));
            connection = (HttpsURLConnection) url.openConnection();
            connection.setConnectTimeout(12_000);
            connection.setReadTimeout(20_000);
            connection.setRequestMethod("GET");
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("User-Agent", "PiStick-FireTV/" + BuildConfig.VERSION_NAME);
            if (bearer) connection.setRequestProperty("Authorization", "Bearer " + credential);
            connection.setInstanceFollowRedirects(false);

            int status = connection.getResponseCode();
            if (status >= 200 && status < 300) {
                String response = read(connection.getInputStream());
                JSONObject payload = new JSONObject(response);
                if (cacheMillis > 0) {
                    cache.put(cacheKey, new CacheEntry(System.currentTimeMillis() + cacheMillis, payload.toString()));
                }
                return payload;
            }
            if (status == HttpURLConnection.HTTP_UNAUTHORIZED || status == HttpURLConnection.HTTP_FORBIDDEN) {
                throw new PiStickException("TMDB rejected that API credential.");
            }
            if (status == HttpURLConnection.HTTP_NOT_FOUND) {
                throw new PiStickException("TMDB could not find that title.");
            }
            throw new PiStickException("TMDB request failed (" + status + ").");
        } catch (PiStickException error) {
            throw error;
        } catch (IOException error) {
            throw new PiStickException("Could not reach TMDB. Check this device's internet connection.", error);
        } catch (JSONException error) {
            throw new PiStickException("TMDB returned an invalid response.", error);
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private JSONObject cached(String key) {
        CacheEntry entry = cache.get(key);
        if (entry == null) return null;
        if (entry.expiresAt <= System.currentTimeMillis()) {
            cache.remove(key);
            return null;
        }
        try {
            return new JSONObject(entry.payload);
        } catch (JSONException error) {
            cache.remove(key);
            return null;
        }
    }

    private static String queryString(Map<String, String> params) {
        List<String> keys = new ArrayList<>(params.keySet());
        Collections.sort(keys);
        StringBuilder result = new StringBuilder();
        for (String key : keys) {
            if (result.length() > 0) result.append('&');
            result.append(encode(key)).append('=').append(encode(params.get(key)));
        }
        return result.toString();
    }

    private static String cacheSafeQuery(Map<String, String> params) {
        return queryString(params);
    }

    private static String encode(String value) {
        try {
            return URLEncoder.encode(value == null ? "" : value, "UTF-8");
        } catch (UnsupportedEncodingException error) {
            throw new IllegalStateException(error);
        }
    }

    private static Map<String, String> parameters(String... pairs) {
        Map<String, String> result = new LinkedHashMap<>();
        for (int index = 0; index + 1 < pairs.length; index += 2) {
            result.put(pairs[index], pairs[index + 1]);
        }
        return result;
    }

    private static String read(InputStream input) throws IOException, PiStickException {
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int total = 0;
            int count;
            while ((count = stream.read(buffer)) != -1) {
                total += count;
                if (total > MAX_RESPONSE_BYTES) throw new PiStickException("TMDB returned too much data.");
                output.write(buffer, 0, count);
            }
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static String truncate(String value, int maximumCodePoints) {
        int count = value.codePointCount(0, value.length());
        if (count <= maximumCodePoints) return value;
        return value.substring(0, value.offsetByCodePoints(0, maximumCodePoints));
    }
}
