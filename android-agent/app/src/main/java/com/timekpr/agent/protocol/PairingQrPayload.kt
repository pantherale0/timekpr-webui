package com.timekpr.agent.protocol

import org.json.JSONObject

/**
 * QR payload emitted by the TimeKpr server for Android (and Linux) agent setup.
 */
data class PairingQrPayload(
    val serverUrl: String,
    val registrationToken: String?,
) {
    companion object {
        const val TYPE = "timekpr_pairing"

        fun parse(raw: String): PairingQrPayload? {
            val trimmed = raw.trim()
            if (trimmed.isEmpty()) return null

            return try {
                val json = JSONObject(trimmed)
                if (json.optString("type") != TYPE) {
                    return null
                }
                val serverUrl = json.optString("server_url").trim()
                if (serverUrl.isEmpty()) {
                    return null
                }
                PairingQrPayload(
                    serverUrl = serverUrl,
                    registrationToken = json.optString("registration_token").takeIf { it.isNotBlank() },
                )
            } catch (_: Exception) {
                // Allow plain WebSocket URL for manual QR encodings.
                if (trimmed.startsWith("ws://") || trimmed.startsWith("wss://")) {
                    PairingQrPayload(serverUrl = trimmed, registrationToken = null)
                } else {
                    null
                }
            }
        }
    }
}
