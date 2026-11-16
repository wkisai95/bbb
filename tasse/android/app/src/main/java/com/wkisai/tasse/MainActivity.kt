package com.wkisai.tasse

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.WebViewAssetLoader
import org.json.JSONObject

/**
 * Guscio dell'app Tasse proprietario: mostra tasse/web (incluso nell'APK). I dati arrivano dai file cifrati
 * che bbb salva nella repository (docs/cifratura.md); l'invito si incolla nell'app o le si condivide da bbb.
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    private var pageReady = false
    private var pendingInvite: String? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val loader = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()
        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.allowFileAccess = false
        web.settings.allowContentAccess = false
        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                loader.shouldInterceptRequest(request.url)

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                if (request.url.host == APP_HOST) return false
                open(request.url.toString())
                return true
            }
        }
        web.addJavascriptInterface(Bridge(), "Android")
        setContentView(web)
        pendingInvite = sharedText(intent)
        web.loadUrl("https://$APP_HOST/assets/index.html")
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val text = sharedText(intent) ?: return
        if (pageReady) deliver(text) else pendingInvite = text
    }

    private fun sharedText(i: Intent?): String? =
        if (i?.action == Intent.ACTION_SEND) i.getStringExtra(Intent.EXTRA_TEXT) else null

    private fun deliver(text: String) = runOnUiThread {
        web.evaluateJavascript("window.tasseInvite && window.tasseInvite(${JSONObject.quote(text)})", null)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() = finish()

    override fun onPause() { web.onPause(); super.onPause() }
    override fun onResume() { super.onResume(); web.onResume() }
    override fun onDestroy() { web.destroy(); super.onDestroy() }

    private fun open(url: String) {
        if (!url.startsWith("https://")) return
        runOnUiThread {
            try { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) } catch (e: Exception) { }
        }
    }

    inner class Bridge {
        /** La pagina è pronta: le consegno l'invito condiviso da bbb, se c'è. */
        @JavascriptInterface
        fun ready() = runOnUiThread {
            pageReady = true
            pendingInvite?.let { deliver(it) }
            pendingInvite = null
        }

        @JavascriptInterface
        fun openExternal(url: String) = open(url)

        @JavascriptInterface
        fun version(): String = try { packageManager.getPackageInfo(packageName, 0).versionName ?: "?" } catch (e: Exception) { "?" }
    }

    companion object {
        private const val APP_HOST = "appassets.androidplatform.net"
    }
}
