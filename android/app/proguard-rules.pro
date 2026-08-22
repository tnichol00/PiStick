# The JavaScript bridge is invoked by name from Android WebView.
-keepclassmembers class app.pistick.android.PiStickBridge {
    @android.webkit.JavascriptInterface <methods>;
}

-keepattributes *Annotation*
