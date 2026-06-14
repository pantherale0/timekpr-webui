package com.guardian.agent.monitor

import android.content.Context
import org.json.JSONObject
import java.io.File

/** Cross-user alert queue: secondary profiles append; user 0 drains via createPackageContextAsUser. */
object PendingAlertStore {
    const val FILE_NAME = "pending_alerts.ndjson"

    fun append(context: Context, eventType: String, linuxUsername: String, details: JSONObject) {
        val line = JSONObject()
            .put("event_type", eventType)
            .put("linux_username", linuxUsername)
            .put("details", details)
            .toString()
        val file = File(context.filesDir, FILE_NAME)
        file.appendText("$line\n")
    }

    fun drain(context: Context): List<PendingAlert> = drainFile(File(context.filesDir, FILE_NAME))

    fun drainFile(file: File): List<PendingAlert> {
        if (!file.exists() || file.length() == 0L) return emptyList()
        val alerts = mutableListOf<PendingAlert>()
        try {
            for (line in file.readLines()) {
                if (line.isBlank()) continue
                val json = JSONObject(line)
                val eventType = json.optString("event_type")
                val username = json.optString("linux_username")
                val details = json.optJSONObject("details") ?: JSONObject()
                if (eventType.isNotBlank() && username.isNotBlank()) {
                    alerts.add(PendingAlert(eventType, username, details))
                }
            }
        } catch (_: Exception) {
        }
        file.writeText("")
        return alerts
    }

    data class PendingAlert(
        val eventType: String,
        val linuxUsername: String,
        val details: JSONObject,
    )
}
