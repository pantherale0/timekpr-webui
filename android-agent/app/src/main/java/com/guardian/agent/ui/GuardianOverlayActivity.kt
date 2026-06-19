package com.guardian.agent.ui

import android.net.Uri
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import com.guardian.agent.monitor.AlertEventBus
import org.json.JSONObject

/**
 * Full-screen Guardian Space overlay activity.
 *
 * Renders the bundled blockedv2.html asset inside a [WebView] and injects
 * [reason], [ageTier], [parentNote], and [deviceName] from the launching [Intent]
 * so the correct age/reason content is shown immediately.
 *
 * Child-initiated access requests are bridged back to the server via
 * [AlertEventBus] (the same channel used by [BlockedDomainOverlay]).
 */
class GuardianOverlayActivity : Activity() {

    private var webView: WebView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Remove all window chrome and keep the overlay on top of the lock screen if needed
        window.addFlags(
            WindowManager.LayoutParams.FLAG_FULLSCREEN or
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD
        )

        setContentView(com.guardian.agent.R.layout.activity_guardian_overlay)

        val reason = intent.getStringExtra(EXTRA_REASON) ?: "sleep"
        val ageTier = intent.getStringExtra(EXTRA_AGE_TIER) ?: "eight12"
        val parentNote = intent.getStringExtra(EXTRA_PARENT_NOTE) ?: ""
        val deviceName = intent.getStringExtra(EXTRA_DEVICE_NAME) ?: ""
        val linuxUsername = intent.getStringExtra(EXTRA_LINUX_USERNAME) ?: "android"

        setupWebView(reason, ageTier, parentNote, deviceName, linuxUsername)
    }

    @SuppressLint("SetJavascriptEnabled")
    private fun setupWebView(
        reason: String,
        ageTier: String,
        parentNote: String,
        deviceName: String,
        linuxUsername: String,
    ) {
        val wv = findViewById<WebView>(com.guardian.agent.R.id.guardian_overlay_webview)
        webView = wv

        wv.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = true
            cacheMode = WebSettings.LOAD_DEFAULT
        }

        // Expose JavascriptInterface so sendQuickRequest / sendCustomRequest in blockedv2.html
        // can reach the server directly without chrome.runtime.sendMessage
        wv.addJavascriptInterface(
            GuardianJsBridge(linuxUsername),
            "guardianBridge",
        )

        wv.webChromeClient = WebChromeClient()
        wv.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView, url: String) {
                // Runtime values are passed via query string; re-apply if the page hot-reloads.
                val safeReason = reason.replace("'", "\\'")
                val safeAge = ageTier.replace("'", "\\'")
                val safeDevice = deviceName.replace("'", "\\'").replace("\"", "\\\"")
                val safeNote = parentNote.replace("'", "\\'").replace("\"", "\\\"")
                view.evaluateJavascript(
                    "setAge('$safeAge'); setReason('$safeReason'); setDeviceInfo('$safeDevice', '$safeNote');",
                    null,
                )
            }
        }

        val lang = java.util.Locale.getDefault().language
        val overlayUrl = buildString {
            append("file:///android_asset/blockedv2.html")
            append("?reason=").append(Uri.encode(reason))
            append("&age=").append(Uri.encode(ageTier))
            append("&device=").append(Uri.encode(deviceName))
            append("&note=").append(Uri.encode(parentNote))
            append("&lang=").append(Uri.encode(lang))
        }
        wv.loadUrl(overlayUrl)
    }

    override fun onDestroy() {
        webView?.destroy()
        webView = null
        super.onDestroy()
    }

    // -------------------------------------------------------------------------
    // Javascript bridge
    // -------------------------------------------------------------------------

    inner class GuardianJsBridge(private val linuxUsername: String) {

        /**
         * Called by blockedv2.html when the child taps a preset or custom request button.
         * Emits an [AlertEventBus] event that the agent service forwards to the server.
         */
        @JavascriptInterface
        fun sendAccessRequest(reason: String, message: String) {
            Log.d(TAG, "Access request from $linuxUsername: reason=$reason message=$message")
            AlertEventBus.emit(
                "access_requested",
                linuxUsername,
                JSONObject()
                    .put("request_type", "guardian_overlay")
                    .put("reason", reason)
                    .put("message", message),
            )
        }
    }

    companion object {
        private const val TAG = "GuardianOverlayActivity"

        const val EXTRA_REASON = "guardian_reason"
        const val EXTRA_AGE_TIER = "guardian_age_tier"
        const val EXTRA_PARENT_NOTE = "guardian_parent_note"
        const val EXTRA_DEVICE_NAME = "guardian_device_name"
        const val EXTRA_LINUX_USERNAME = "guardian_linux_username"

        /**
         * Build a launch [Intent] for [GuardianOverlayActivity] with all overlay params set.
         */
        fun buildIntent(
            context: Context,
            reason: String,
            ageTier: String?,
            parentNote: String?,
            deviceName: String?,
            linuxUsername: String,
        ): Intent = Intent(context, GuardianOverlayActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            putExtra(EXTRA_REASON, reason)
            putExtra(EXTRA_AGE_TIER, ageTier ?: "eight12")
            putExtra(EXTRA_PARENT_NOTE, parentNote ?: "")
            putExtra(EXTRA_DEVICE_NAME, deviceName ?: "")
            putExtra(EXTRA_LINUX_USERNAME, linuxUsername)
        }
    }
}
