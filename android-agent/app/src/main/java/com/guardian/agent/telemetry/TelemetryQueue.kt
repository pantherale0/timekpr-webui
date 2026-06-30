package com.guardian.agent.telemetry

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID

/**
 * Offline buffer for web/video telemetry (mirrors extension chrome.storage.local queues).
 */
class TelemetryQueue internal constructor(private val queueFile: File) {

    constructor(context: Context) : this(File(context.filesDir, FILE_NAME))

    fun appendBrowserLog(linuxUsername: String, logEntry: JSONObject) {
        append(
            kind = KIND_BROWSER,
            linuxUsername = linuxUsername,
            payload = JSONObject().put("logs", JSONArray().put(logEntry)),
        )
    }

    fun appendVideoLog(linuxUsername: String, platform: String, logEntry: JSONObject) {
        append(
            kind = KIND_VIDEO,
            linuxUsername = linuxUsername,
            payload = JSONObject()
                .put("platform", platform)
                .put("logs", JSONArray().put(logEntry)),
        )
    }

    fun pendingCount(): Int {
        if (!queueFile.exists()) return 0
        return queueFile.readLines().count { it.isNotBlank() }
    }

    fun drainForFlush(maxEntries: Int = 200): List<QueuedEntry> {
        if (!queueFile.exists() || queueFile.length() == 0L) {
            return emptyList()
        }
        val entries = mutableListOf<QueuedEntry>()
        val remaining = mutableListOf<String>()
        try {
            for (line in queueFile.readLines()) {
                if (line.isBlank()) continue
                val entry = parseLine(line) ?: continue
                if (entries.size < maxEntries) {
                    entries.add(entry)
                } else {
                    remaining.add(line)
                }
            }
        } catch (_: Exception) {
            return emptyList()
        }
        queueFile.writeText(remaining.joinToString("\n", postfix = if (remaining.isEmpty()) "" else "\n"))
        return entries
    }

    fun removeIds(ids: Collection<String>) {
        if (ids.isEmpty() || !queueFile.exists()) return
        val idSet = ids.toSet()
        val kept = queueFile.readLines().filter { line ->
            line.isBlank() || parseLine(line)?.id !in idSet
        }
        queueFile.writeText(kept.joinToString("\n", postfix = if (kept.isEmpty()) "" else "\n"))
    }

    private fun append(kind: String, linuxUsername: String, payload: JSONObject) {
        val entry = JSONObject()
            .put("id", UUID.randomUUID().toString())
            .put("kind", kind)
            .put("linux_username", linuxUsername)
            .put("payload", payload)
            .put("created_at", System.currentTimeMillis())
        synchronized(queueFile) {
            queueFile.appendText(entry.toString() + "\n")
        }
    }

    private fun parseLine(line: String): QueuedEntry? {
        return try {
            val json = JSONObject(line)
            val id = json.optString("id")
            val kind = json.optString("kind")
            val username = json.optString("linux_username")
            val payload = json.optJSONObject("payload") ?: JSONObject()
            if (id.isBlank() || kind.isBlank() || username.isBlank()) {
                null
            } else {
                QueuedEntry(id, kind, username, payload)
            }
        } catch (_: Exception) {
            null
        }
    }

    data class QueuedEntry(
        val id: String,
        val kind: String,
        val linuxUsername: String,
        val payload: JSONObject,
    )

    companion object {
        const val FILE_NAME = "telemetry_queue.ndjson"
        const val KIND_BROWSER = "browser_log"
        const val KIND_VIDEO = "video_log"
        const val MAX_BROWSER_QUEUE = 2000
        const val MAX_VIDEO_QUEUE = 1000
    }
}
