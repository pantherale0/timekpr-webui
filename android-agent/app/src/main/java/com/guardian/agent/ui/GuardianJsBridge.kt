package com.guardian.agent.ui

import android.util.Log
import android.webkit.JavascriptInterface
import com.guardian.agent.monitor.AlertEventBus
import com.guardian.agent.telemetry.AgentTelemetryRouter
import org.json.JSONObject

/**
 * WebView bridge for blockedv2.html when rendered outside the Chrome extension.
 */
class GuardianJsBridge(
    private val linuxUsername: String,
    private val telemetryRouter: AgentTelemetryRouter,
) {
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

    @JavascriptInterface
    fun requestRegistration(domain: String): String {
        val response = telemetryRouter.requestRegistration(linuxUsername, domain.trim())
        return response.toString()
    }

    @JavascriptInterface
    fun checkRegistration(domain: String): String {
        val response = telemetryRouter.checkRegistration(linuxUsername, domain.trim())
        return response.toString()
    }

    companion object {
        private const val TAG = "GuardianJsBridge"
    }
}
