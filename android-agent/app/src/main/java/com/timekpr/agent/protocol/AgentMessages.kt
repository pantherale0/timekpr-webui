package com.timekpr.agent.protocol

import org.json.JSONArray
import org.json.JSONObject

object AgentMessages {
    fun hello(
        systemId: String,
        hostname: String,
        registrationToken: String?,
        agentVersion: String,
        linuxUsers: List<Map<String, Any>>,
        platform: String = "android",
    ): String {
        val payload = JSONObject()
            .put("type", "hello")
            .put("system_id", systemId)
            .put("system_hostname", hostname)
            .put("agent_version", agentVersion)
            .put("platform", platform)
            .put("linux_users", JSONArray(linuxUsers.map { JSONObject(it) }))
        if (!registrationToken.isNullOrBlank()) {
            payload.put("registration_token", registrationToken)
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
}
