package pk.edu.bahria.policy;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Locale;

public class MainActivity extends Activity {
    private static final int MIC_REQUEST = 91;

    private String serverHost;
    private String serverUrl;
    private WebView webView;
    private SpeechRecognizer speech;
    private TextToSpeech tts;
    private boolean pendingListen;
    private boolean listening;
    private int listenRetries;
    private int listenGeneration;
    private String lastHeard = "";
    private final Handler main = new Handler(Looper.getMainLooper());

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        serverHost = getString(R.string.server_host);
        serverUrl = getString(R.string.server_url);
        webView = new WebView(this);
        webView.setBackgroundColor(0xFF0B0B12);
        setContentView(webView);

        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(webView, true);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setSupportZoom(false);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        webView.addJavascriptInterface(new BahriaVoice(), "BahriaVoice");
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                main.post(() -> request.grant(request.getResources()));
            }
        });
        webView.setWebViewClient(new BahriaClient());
        tts = new TextToSpeech(this, status -> {
            if (status == TextToSpeech.SUCCESS && tts != null) {
                tts.setLanguage(Locale.US);
            }
        });
        webView.loadUrl(serverUrl + "/");
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (speech != null) {
            speech.destroy();
            speech = null;
        }
        if (tts != null) {
            tts.stop();
            tts.shutdown();
            tts = null;
        }
        super.onDestroy();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        boolean granted = grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED;
        if (requestCode == MIC_REQUEST && pendingListen) {
            pendingListen = false;
            if (granted) {
                startListening();
            } else {
                emit("error", "Please allow microphone access for BahriaAI.");
            }
        }
    }

    private boolean hasMicPermission() {
        return checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    private void stopListening() {
        listening = false;
        if (speech != null) {
            try {
                speech.cancel();
            } catch (Exception ignored) {
            }
            try {
                speech.destroy();
            } catch (Exception ignored) {
            }
            speech = null;
        }
    }

    private void startListening() {
        if (!hasMicPermission()) {
            pendingListen = true;
            requestPermissions(new String[] { Manifest.permission.RECORD_AUDIO }, MIC_REQUEST);
            return;
        }
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            listening = false;
            emit("error", "Voice recognition is not available on this phone. Please type your question.");
            return;
        }
        if (tts != null) tts.stop();
        stopListening();
        if (listenRetries == 0) lastHeard = "";
        emit("start", lastHeard);
        listening = true;
        final int generation = ++listenGeneration;
        speech = SpeechRecognizer.createSpeechRecognizer(this);
        speech.setRecognitionListener(new RecognitionListener() {
            @Override
            public void onReadyForSpeech(Bundle params) {}

            @Override
            public void onBeginningOfSpeech() {}

            @Override
            public void onRmsChanged(float rmsdB) {}

            @Override
            public void onBufferReceived(byte[] buffer) {}

            @Override
            public void onEndOfSpeech() {
                if (generation != listenGeneration || speech == null) return;
                try {
                    speech.stopListening();
                } catch (Exception ignored) {
                }
            }

            @Override
            public void onError(int error) {
                if (generation != listenGeneration || !listening) return;
                if (!lastHeard.isEmpty()) {
                    finishHeard(lastHeard);
                    return;
                }
                boolean retryable = error == SpeechRecognizer.ERROR_NO_MATCH
                    || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT
                    || error == SpeechRecognizer.ERROR_CLIENT
                    || error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY;
                if (retryable && listenRetries < 1) {
                    listenRetries += 1;
                    main.postDelayed(MainActivity.this::startListening, 350);
                    return;
                }
                listening = false;
                emit("error", messageForSpeechError(error));
            }

            @Override
            public void onResults(Bundle results) {
                if (generation != listenGeneration) return;
                String text = firstResult(results);
                if (text.isEmpty()) text = lastHeard;
                if (text.isEmpty()) {
                    listening = false;
                    emit("error", "Could not hear that. Please try the mic again.");
                } else {
                    finishHeard(text);
                }
            }

            @Override
            public void onPartialResults(Bundle partialResults) {
                if (generation != listenGeneration) return;
                String text = firstResult(partialResults);
                if (text.isEmpty()) return;
                lastHeard = text;
                emit("partial", text);
            }

            @Override
            public void onEvent(int eventType, Bundle params) {}
        });
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag());
        intent.putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, getPackageName());
        intent.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1500);
        intent.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 1500);
        speech.startListening(intent);
    }

    private void finishHeard(String text) {
        listening = false;
        listenRetries = 0;
        lastHeard = "";
        listenGeneration++;
        emit("final", text);
        if (speech != null) {
            main.post(this::stopListening);
        }
    }

    private String firstResult(Bundle results) {
        if (results == null) return "";
        String text = firstMatch(results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION));
        if (!text.isEmpty()) return text;
        return firstMatch(results.getStringArrayList("android.speech.extra.UNSTABLE_TEXT"));
    }

    private static String firstMatch(ArrayList<String> matches) {
        if (matches == null || matches.isEmpty() || matches.get(0) == null) return "";
        return matches.get(0).trim();
    }

    private String messageForSpeechError(int error) {
        switch (error) {
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS:
                return "Please allow microphone access for BahriaAI.";
            case SpeechRecognizer.ERROR_NETWORK:
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT:
                return "Voice needs an internet connection. Check Wi-Fi and try again.";
            case SpeechRecognizer.ERROR_NO_MATCH:
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT:
                return "Could not hear that. Please try the mic again.";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY:
                return "Mic is busy. Wait a second and try again.";
            case SpeechRecognizer.ERROR_CLIENT:
                return "Mic could not start. Please try again.";
            default:
                return "Could not hear that. Please try the mic again.";
        }
    }

    private void emit(String type, String text) {
        String payload = "{\"type\":" + jsString(type) + ",\"text\":" + jsString(text) + "}";
        String js = "window.dispatchEvent(new CustomEvent('bahria-voice',{detail:" + payload + "}))";
        main.post(() -> webView.evaluateJavascript(js, null));
    }

    private static String jsString(String raw) {
        if (raw == null) return "\"\"";
        return "\""
            + raw.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "")
            + "\"";
    }

    private class BahriaVoice {
        @JavascriptInterface
        public boolean isNative() {
            return true;
        }

        @JavascriptInterface
        public void start() {
            main.post(() -> {
                listenRetries = 0;
                startListening();
            });
        }

        @JavascriptInterface
        public void stop() {
            main.post(MainActivity.this::stopListening);
        }

        @JavascriptInterface
        public void speak(String text) {
            main.post(() -> {
                if (tts == null || text == null || text.isEmpty()) return;
                tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "bahria-reply");
            });
        }

        @JavascriptInterface
        public void silence() {
            main.post(() -> {
                if (tts != null) tts.stop();
            });
        }
    }

    private boolean assetExists(String path) {
        try (InputStream ignored = getAssets().open(path)) {
            return true;
        } catch (IOException error) {
            return false;
        }
    }

    private WebResourceResponse fromAsset(String path, String mime) {
        try {
            InputStream stream = getAssets().open(path);
            return new WebResourceResponse(
                mime,
                "UTF-8",
                200,
                "OK",
                Collections.singletonMap("Cache-Control", "no-cache"),
                stream
            );
        } catch (IOException error) {
            return null;
        }
    }

    private String mimeFor(String path) {
        String lower = path.toLowerCase();
        if (lower.endsWith(".js")) return "application/javascript";
        if (lower.endsWith(".css")) return "text/css";
        if (lower.endsWith(".html")) return "text/html";
        if (lower.endsWith(".json")) return "application/json";
        if (lower.endsWith(".svg")) return "image/svg+xml";
        if (lower.endsWith(".png")) return "image/png";
        if (lower.endsWith(".woff2")) return "font/woff2";
        if (lower.endsWith(".woff")) return "font/woff";
        return "application/octet-stream";
    }

    private boolean isSpaRoute(String path) {
        if (path.equals("/") || path.equals("/index.html")) return true;
        if (path.equals("/login") || path.startsWith("/login/")) return true;
        if (path.equals("/console") || path.startsWith("/console/")) return true;
        return false;
    }

    private boolean isBackendPath(String path) {
        return path.startsWith("/api/")
            || path.equals("/api")
            || path.startsWith("/media/")
            || path.startsWith("/static/")
            || path.startsWith("/django-admin");
    }

    private class BahriaClient extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            return false;
        }

        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            if (!"GET".equalsIgnoreCase(request.getMethod())) {
                return null;
            }
            Uri uri = request.getUrl();
            String host = uri.getHost();
            if (host == null || !host.equals(serverHost)) {
                return null;
            }
            String path = uri.getPath();
            if (path == null || path.isEmpty()) {
                path = "/";
            }
            if (isBackendPath(path)) {
                return null;
            }
            if (isSpaRoute(path) && assetExists("www/index.html")) {
                return fromAsset("www/index.html", "text/html");
            }
            String assetPath = "www" + path;
            if (assetExists(assetPath)) {
                return fromAsset(assetPath, mimeFor(path));
            }
            return null;
        }
    }
}
