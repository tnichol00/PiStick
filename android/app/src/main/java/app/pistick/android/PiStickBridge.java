package app.pistick.android;

import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

final class PiStickBridge implements AutoCloseable {
    private static final int MAX_MESSAGE_LENGTH = 256 * 1024;

    private final MainActivity activity;
    private final WebView webView;
    private final String secret;
    private final PiStickApi api;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService requestExecutor = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "PiStick-API");
        thread.setPriority(Thread.NORM_PRIORITY - 1);
        return thread;
    });
    private final AtomicBoolean closed = new AtomicBoolean(false);

    PiStickBridge(MainActivity activity, WebView webView, String secret) {
        this.activity = activity;
        this.webView = webView;
        this.secret = secret;
        this.api = new PiStickApi(activity.getApplicationContext());
    }

    @JavascriptInterface
    public void postMessage(String encodedRequest) {
        if (closed.get() || encodedRequest == null || encodedRequest.length() > MAX_MESSAGE_LENGTH) return;
        final JSONObject request;
        final String requestId;
        try {
            request = new JSONObject(encodedRequest);
            requestId = request.optString("id", "");
            if (!requestId.matches("[0-9]{1,20}") || !validSecret(request.optString("secret", ""))) return;
        } catch (JSONException error) {
            return;
        }

        requestExecutor.execute(() -> {
            if (closed.get()) return;
            try {
                String path = request.optString("path", "");
                String method = request.optString("method", "GET");
                JSONObject body = request.optJSONObject("body");
                JSONObject result = api.handle(path, method, body == null ? new JSONObject() : body);
                respond(requestId, true, result);
            } catch (Throwable error) {
                JSONObject payload = new JSONObject();
                JsonUtils.put(payload, "error", JsonUtils.errorMessage(error));
                respond(requestId, false, payload);
            }
        });
    }

    @JavascriptInterface
    public void showKeyboard(String candidateSecret) {
        if (closed.get() || !validSecret(candidateSecret)) return;
        mainHandler.post(activity::showSoftKeyboard);
    }

    @JavascriptInterface
    public void hideKeyboard(String candidateSecret) {
        if (closed.get() || !validSecret(candidateSecret)) return;
        mainHandler.post(activity::hideSoftKeyboard);
    }

    @JavascriptInterface
    public void requestPlayerAutostart(String candidateSecret) {
        if (closed.get() || !validSecret(candidateSecret)) return;
        mainHandler.post(activity::requestPlayerAutostart);
    }

    @JavascriptInterface
    public void sendPlayerKey(String candidateSecret, String action) {
        if (closed.get() || !validSecret(candidateSecret)) return;
        mainHandler.post(() -> activity.sendPlayerKey(action));
    }

    private boolean validSecret(String candidate) {
        return MessageDigest.isEqual(
                secret.getBytes(StandardCharsets.UTF_8),
                candidate.getBytes(StandardCharsets.UTF_8)
        );
    }

    private void respond(String requestId, boolean succeeded, JSONObject payload) {
        String encoded = Base64.encodeToString(
                payload.toString().getBytes(StandardCharsets.UTF_8),
                Base64.NO_WRAP
        );
        String script = "window.PiStickNative.receive(\"" + requestId + "\","
                + (succeeded ? "true" : "false") + ",\"" + encoded + "\");";
        mainHandler.post(() -> {
            if (!closed.get() && !activity.isFinishing() && !activity.isDestroyed()) {
                webView.evaluateJavascript(script, null);
            }
        });
    }

    @Override
    public void close() {
        if (!closed.compareAndSet(false, true)) return;
        requestExecutor.shutdownNow();
        api.close();
    }
}
