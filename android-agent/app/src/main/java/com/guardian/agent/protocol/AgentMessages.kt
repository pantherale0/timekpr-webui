package com.guardian.agent.protocol

import org.json.JSONArray
import org.json.JSONObject

object AgentMessages {
    fun hello(
        systemId: String,
        hostname: String,
        registrationToken: String?,
        agentVersion: String,
        linuxUsers: List<Map<String, Any>>,
        paired: Boolean,
        platform: String = "android",
        fcmToken: String? = null,
        isDeviceOwner: Boolean = false,
    ): String {
        val payload = JSONObject()
            .put("type", "hello")
            .put("system_id", systemId)
            .put("system_hostname", hostname)
            .put("agent_version", agentVersion)
            .put("platform", platform)
            .put("linux_users", JSONArray(linuxUsers.map { JSONObject(it) }))
            .put("paired", paired)
            .put("is_device_owner", isDeviceOwner)
        if (!registrationToken.isNullOrBlank()) {
            payload.put("registration_token", registrationToken)
        }
        if (!fcmToken.isNullOrBlank()) {
            payload.put("fcm_token", fcmToken)
        }
        return payload.toString()
    }

    fun register(systemId: String, signature: String): String {
        return JSONObject()
            .put("type", "register")
            .put("system_id", systemId)
            .put("signature", signature)
            .toString()
    }

    fun commandResponse(
        correlationId: String?,
        success: Boolean,
        message: String,
        data: JSONObject = JSONObject(),
    ): String {
        return JSONObject()
            .put("type", "command_response")
            .put("correlation_id", correlationId)
            .put("success", success)
            .put("message", message)
            .put("data", data)
            .toString()
    }

    fun alertEvent(
        eventType: String,
        occurredAt: String,
        linuxUsername: String,
        details: JSONObject,
    ): String {
        return JSONObject()
            .put("type", "alert_event")
            .put("event_type", eventType)
            .put("occurred_at", occurredAt)
            .put("linux_username", linuxUsername)
            .put("details", details)
            .toString()
    }

    fun policySyncCheck(sourceRevisions: Map<String, String>): String {
        val revisions = JSONObject()
        sourceRevisions.forEach { (key, value) -> revisions.put(key, value) }
        return JSONObject()
            .put("type", "policy_sync_check")
            .put("source_revisions", revisions)
            .toString()
    }

    fun installedAppsReport(
        reportId: String,
        linuxUsername: String,
        chunkIndex: Int,
        chunkTotal: Int,
        isFinal: Boolean,
        reportedAt: String,
        apps: List<JSONObject>,
    ): String {
        return JSONObject()
            .put("type", "installed_apps_report")
            .put("report_id", reportId)
            .put("linux_username", linuxUsername)
            .put("chunk_index", chunkIndex)
            .put("chunk_total", chunkTotal)
            .put("is_final", isFinal)
            .put("reported_at", reportedAt)
            .put("apps", org.json.JSONArray(apps))
            .toString()
    }

    fun appIconReport(contentHash: String, mimeType: String, dataBase64: String): String {
        return JSONObject()
            .put("type", "app_icon_report")
            .put("content_hash", contentHash)
            .put("mime_type", mimeType)
            .put("data_base64", dataBase64)
            .toString()
    }
}
