package app.pistick.android;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

final class JsonUtils {
    private JsonUtils() {}

    static JSONObject object() {
        return new JSONObject();
    }

    static JSONObject object(Object value) {
        return value instanceof JSONObject ? (JSONObject) value : null;
    }

    static JSONArray array(Object value) {
        return value instanceof JSONArray ? (JSONArray) value : new JSONArray();
    }

    static JSONObject copy(JSONObject value) {
        if (value == null) return new JSONObject();
        try {
            return new JSONObject(value.toString());
        } catch (JSONException ignored) {
            return new JSONObject();
        }
    }

    static JSONArray copy(JSONArray value) {
        if (value == null) return new JSONArray();
        try {
            return new JSONArray(value.toString());
        } catch (JSONException ignored) {
            return new JSONArray();
        }
    }

    static String string(Object value) {
        if (value == null || value == JSONObject.NULL) return "";
        return String.valueOf(value);
    }

    static int integer(Object value, int fallback) {
        if (value instanceof Number) return ((Number) value).intValue();
        try {
            return Integer.parseInt(string(value));
        } catch (NumberFormatException ignored) {
            return fallback;
        }
    }

    static double number(Object value, double fallback) {
        if (value instanceof Number) return ((Number) value).doubleValue();
        try {
            return Double.parseDouble(string(value));
        } catch (NumberFormatException ignored) {
            return fallback;
        }
    }

    static boolean isNull(Object value) {
        return value == null || value == JSONObject.NULL;
    }

    static JSONObject put(JSONObject target, String key, Object value) {
        try {
            target.put(key, value == null ? JSONObject.NULL : value);
            return target;
        } catch (JSONException error) {
            throw new IllegalArgumentException("Invalid JSON value for " + key, error);
        }
    }

    static JSONArray add(JSONArray target, Object value) {
        target.put(value == null ? JSONObject.NULL : value);
        return target;
    }

    static List<JSONObject> objects(JSONArray array) {
        List<JSONObject> values = new ArrayList<>();
        if (array == null) return values;
        for (int index = 0; index < array.length(); index++) {
            JSONObject value = array.optJSONObject(index);
            if (value != null) values.add(value);
        }
        return values;
    }

    static JSONObject selected(JSONObject source, String... keys) {
        JSONObject result = new JSONObject();
        if (source == null) return result;
        for (String key : keys) {
            Object value = source.opt(key);
            if (!isNull(value)) put(result, key, value);
        }
        return result;
    }

    static String errorMessage(Throwable error) {
        Throwable current = error;
        while (current.getCause() != null && current.getCause() != current) {
            current = current.getCause();
        }
        String message = error.getMessage();
        if (message == null || message.trim().isEmpty()) message = current.getMessage();
        return message == null || message.trim().isEmpty() ? "PiStick could not complete that request." : message;
    }
}
