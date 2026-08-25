package app.pistick.android;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.util.Base64;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;

public final class MainActivity extends Activity {
    private static final int BACKGROUND = Color.rgb(9, 9, 11);
    private static final String ASSET_ROOT = "file:///android_asset/web/";

    private FrameLayout root;
    private WebView webView;
    private PiStickBridge bridge;
    private View customView;
    private WebChromeClient.CustomViewCallback customViewCallback;
    private final AtomicBoolean backPending = new AtomicBoolean(false);

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(BACKGROUND);
        getWindow().setNavigationBarColor(BACKGROUND);
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        hideSystemUi();

        root = new FrameLayout(this);
        root.setBackgroundColor(BACKGROUND);
        setContentView(root);
        createWebView();

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,
                    this::handleBack
            );
        }
    }

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    private void createWebView() {
        if (isFinishing() || isDestroyed()) return;
        WebView.setWebContentsDebuggingEnabled(false);
        WebView candidate = new WebView(this);
        candidate.setBackgroundColor(BACKGROUND);
        candidate.setLayerType(View.LAYER_TYPE_HARDWARE, null);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            candidate.setRendererPriorityPolicy(WebView.RENDERER_PRIORITY_BOUND, true);
        }
        candidate.setOverScrollMode(View.OVER_SCROLL_NEVER);
        candidate.setFocusable(true);
        candidate.setFocusableInTouchMode(true);

        android.webkit.WebSettings settings = candidate.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(false);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccess(true);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setCacheMode(android.webkit.WebSettings.LOAD_DEFAULT);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setSupportZoom(false);
        settings.setTextZoom(100);
        settings.setOffscreenPreRaster(true);
        settings.setUseWideViewPort(true);
        settings.setUserAgentString(settings.getUserAgentString() + " PiStick-FireTV/" + BuildConfig.VERSION_NAME);

        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(candidate, true);

        String secret = bridgeSecret();
        PiStickBridge candidateBridge = new PiStickBridge(this, candidate, secret);
        candidate.addJavascriptInterface(candidateBridge, "PiStickAndroid");
        candidate.setWebViewClient(Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new RendererAwareWebViewClient()
                : new PiStickWebViewClient());
        candidate.setWebChromeClient(new PiStickChromeClient());

        webView = candidate;
        bridge = candidateBridge;
        root.addView(candidate, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));

        try {
            String html = readAsset("web/index.html");
            String injection = "<script>window.__PISTICK_ANDROID_SECRET__='" + secret
                    + "';window.__PISTICK_FIRE_TV__=true;"
                    + "document.documentElement.classList.add('fire-tv');</script>";
            html = html.replace("</head>", injection + "</head>");
            candidate.loadDataWithBaseURL(ASSET_ROOT, html, "text/html", StandardCharsets.UTF_8.name(), null);
            candidate.requestFocus(View.FOCUS_DOWN);
        } catch (IOException error) {
            showFatalError("PiStick resources are missing. Reinstall the app.");
        }
    }

    private void showFatalError(String message) {
        if (webView != null) {
            root.removeView(webView);
            destroyWebView();
        }
        TextView label = new TextView(this);
        label.setText(message);
        label.setTextColor(Color.WHITE);
        label.setTextSize(18);
        label.setGravity(android.view.Gravity.CENTER);
        label.setPadding(32, 32, 32, 32);
        root.addView(label, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));
    }

    private void handleBack() {
        if (customView != null) {
            hideCustomView();
            return;
        }
        WebView current = webView;
        if (current == null || !backPending.compareAndSet(false, true)) return;
        String script = "(function(){return Boolean(window.PiStickFireTV"
                + "&&window.PiStickFireTV.handle('back'));})()";
        current.evaluateJavascript(script, value -> {
            backPending.set(false);
            if (!"true".equals(value)) finishAfterTransition();
        });
    }

    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) handleBack();
        else super.onBackPressed();
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        String action = FireTvRemote.actionForKeyCode(event.getKeyCode());
        if (action == null) return super.dispatchKeyEvent(event);
        if (customView != null && !FireTvRemote.BACK.equals(action)) {
            return super.dispatchKeyEvent(event);
        }

        if (event.getAction() == KeyEvent.ACTION_UP) {
            if (FireTvRemote.BACK.equals(action)) handleBack();
            return true;
        }
        if (event.getAction() != KeyEvent.ACTION_DOWN) return true;
        if (FireTvRemote.BACK.equals(action)) return true;
        if (event.getRepeatCount() > 0 && !FireTvRemote.isRepeatable(action)) return true;
        dispatchRemoteAction(action);
        return true;
    }

    private void dispatchRemoteAction(String action) {
        WebView current = webView;
        if (current == null) return;
        current.evaluateJavascript(
                "window.PiStickFireTV&&window.PiStickFireTV.handle('" + action + "');",
                null
        );
    }

    @Override
    protected void onPause() {
        if (webView != null) webView.onPause();
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        hideSystemUi();
        if (webView != null) webView.onResume();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus && customView == null) hideSystemUi();
    }

    @Override
    protected void onDestroy() {
        hideCustomView();
        destroyWebView();
        super.onDestroy();
    }

    private void destroyWebView() {
        PiStickBridge oldBridge = bridge;
        bridge = null;
        if (oldBridge != null) oldBridge.close();
        WebView oldWebView = webView;
        webView = null;
        if (oldWebView != null) {
            oldWebView.removeJavascriptInterface("PiStickAndroid");
            oldWebView.stopLoading();
            oldWebView.setWebChromeClient(null);
            oldWebView.setWebViewClient(null);
            if (oldWebView.getParent() instanceof ViewGroup) {
                ((ViewGroup) oldWebView.getParent()).removeView(oldWebView);
            }
            oldWebView.destroy();
        }
    }

    private void showCustomView(View view, WebChromeClient.CustomViewCallback callback) {
        if (customView != null) {
            callback.onCustomViewHidden();
            return;
        }
        customView = view;
        customViewCallback = callback;
        if (webView != null) webView.setVisibility(View.GONE);
        root.addView(view, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        );
    }

    private void hideCustomView() {
        if (customView == null) return;
        root.removeView(customView);
        customView = null;
        if (webView != null) webView.setVisibility(View.VISIBLE);
        hideSystemUi();
        if (customViewCallback != null) customViewCallback.onCustomViewHidden();
        customViewCallback = null;
    }

    private String readAsset(String path) throws IOException {
        try (InputStream input = getAssets().open(path);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static String bridgeSecret() {
        byte[] random = new byte[32];
        new SecureRandom().nextBytes(random);
        return Base64.encodeToString(random, Base64.NO_WRAP | Base64.NO_PADDING | Base64.URL_SAFE);
    }

    private void hideSystemUi() {
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        );
    }

    private class PiStickWebViewClient extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri url = request.getUrl();
            String scheme = url.getScheme() == null ? "" : url.getScheme().toLowerCase(Locale.ROOT);
            if (request.isForMainFrame()) {
                return !("file".equals(scheme) || "about".equals(scheme));
            }
            return !("https".equals(scheme) || "about".equals(scheme));
        }

        @Override
        public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
            handler.cancel();
        }

    }

    @android.annotation.TargetApi(Build.VERSION_CODES.O)
    private final class RendererAwareWebViewClient extends PiStickWebViewClient {
        @Override
        public boolean onRenderProcessGone(
                WebView view,
                android.webkit.RenderProcessGoneDetail detail
        ) {
            hideCustomView();
            destroyWebView();
            root.post(MainActivity.this::createWebView);
            return true;
        }
    }

    private final class PiStickChromeClient extends WebChromeClient {
        @Override
        public void onShowCustomView(View view, CustomViewCallback callback) {
            showCustomView(view, callback);
        }

        @Override
        public void onHideCustomView() {
            hideCustomView();
        }

        @Override
        public void onPermissionRequest(PermissionRequest request) {
            request.deny();
        }

        @Override
        public boolean onCreateWindow(
                WebView view,
                boolean isDialog,
                boolean isUserGesture,
                android.os.Message resultMsg
        ) {
            return false;
        }

        @Override
        public boolean onShowFileChooser(
                WebView webView,
                ValueCallback<Uri[]> filePathCallback,
                FileChooserParams fileChooserParams
        ) {
            filePathCallback.onReceiveValue(null);
            return true;
        }
    }
}
