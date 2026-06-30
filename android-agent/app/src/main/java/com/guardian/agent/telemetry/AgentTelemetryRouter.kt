package com.guardian.agent.telemetry

import android.content.Context
import com.guardian.agent.GuardianApplication
import com.guardian.agent.config.AgentConfigStore
import com.guardian.agent.monitor.AlertEventBus
import com.guardian.agent.util.AndroidUsers
import com.guardian.agent.work.TelemetryFlushWorker
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.format.DateTimeFormatter

/**
 * Routes extension-equivalent IPC requests to REST endpoints with offline queuing for logs.
 */
class AgentTelemetryRouter(private val context: Context) {
    private val configStore = AgentConfigStore(context.applicationContext)
    private val queue = TelemetryQueue(context.applicationContext)

    fun handleFramedRequest(payload: ByteArray, linuxUsername: String? = null): JSONObject {
        val username = linuxUsername?.takeIf { it.isNotBlank() }
            ?: AndroidUsers.currentLinuxUsername(context.applicationContext)
        return try {
            val request = JSONObject(String(payload, Charsets.UTF_8))
            dispatch(request, username)
        } catch (e: Exception) {
            JSONObject()
                .put("success", false)
                .put("message", "Invalid IPC message structure: ${e.message}")
        }
    }

    fun flushQueuedTelemetry(): Int {
        val config = configStore.load()
        if (config.serverUrl.isBlank() || config.agentToken.isNullOrBlank()) {
            return 0
        }

        val entries = queue.drainForFlush()
        if (entries.isEmpty()) return 0

        val browserBatch = mutableListOf<QueuedEntryRef>()
        val videoByPlatform = linkedMapOf<String, MutableList<QueuedEntryRef>>()

        for (entry in entries) {
            when (entry.kind) {
                TelemetryQueue.KIND_BROWSER -> browserBatch.add(QueuedEntryRef(entry))
                TelemetryQueue.KIND_VIDEO -> {
                    val platform = entry.payload.optString("platform", "youtube")
                    videoByPlatform.getOrPut(platform) { mutableListOf() }
                        .add(QueuedEntryRef(entry))
                }
            }
        }

        var flushed = 0
        val removedIds = mutableListOf<String>()

        if (browserBatch.isNotEmpty()) {
            val logs = JSONArray()
            browserBatch.forEach { ref ->
                val batchLogs = ref.entry.payload.optJSONArray("logs") ?: JSONArray()
                for (index in 0 until batchLogs.length()) {
                    logs.put(batchLogs.getJSONObject(index))
                }
            }
            val body = JSONObject()
                .put("linux_username", browserBatch.first().entry.linuxUsername)
                .put("logs", logs)
            val result = AgentRestClient.postJson(config, "/api/browser/log", body)
            if (result.success) {
                removedIds.addAll(browserBatch.map { it.entry.id })
                flushed += browserBatch.size
            } else {
                requeue(browserBatch.map { it.entry })
            }
        }

        for ((platform, batch) in videoByPlatform) {
            val logs = JSONArray()
            batch.forEach { ref ->
                val batchLogs = ref.entry.payload.optJSONArray("logs") ?: JSONArray()
                for (index in 0 until batchLogs.length()) {
                    logs.put(batchLogs.getJSONObject(index))
                }
            }
            val body = JSONObject()
                .put("linux_username", batch.first().entry.linuxUsername)
                .put("platform", platform)
                .put("logs", logs)
            val result = AgentRestClient.postJson(config, "/api/video/log", body)
            if (result.success) {
                removedIds.addAll(batch.map { it.entry.id })
                flushed += batch.size
            } else {
                requeue(batch.map { it.entry })
            }
        }

        queue.removeIds(removedIds)
        return flushed
    }

    fun queueBrowserLog(linuxUsername: String, url: String, title: String, domain: String) {
        val visitedAt = DateTimeFormatter.ISO_INSTANT.format(Instant.now())
        val logEntry = JSONObject()
            .put("url", url)
            .put("title", title)
            .put("domain", domain)
            .put("visited_at", visitedAt)
        val body = JSONObject()
            .put("linux_username", linuxUsername)
            .put("logs", JSONArray().put(logEntry))
        postOrQueueBrowser(linuxUsername, body, JSONArray().put(logEntry))
    }

    fun queueVideoLog(
        linuxUsername: String,
        platform: String,
        videoId: String,
        title: String,
        channelName: String,
        channelId: String,
        durationSeconds: Int,
    ) {
        val watchedAt = DateTimeFormatter.ISO_INSTANT.format(Instant.now())
        val logEntry = JSONObject()
            .put("video_id", videoId)
            .put("title", title)
            .put("channel_name", channelName)
            .put("channel_id", channelId)
            .put("duration_seconds", durationSeconds)
            .put("watched_at", watchedAt)
        val body = JSONObject()
            .put("linux_username", linuxUsername)
            .put("platform", platform)
            .put("logs", JSONArray().put(logEntry))
        postOrQueueVideo(linuxUsername, platform, body, JSONArray().put(logEntry))
    }

    fun checkRegistration(linuxUsername: String, domain: String): JSONObject {
        return handleCheckRegistration(linuxUsername, domain)
    }

    fun requestRegistration(linuxUsername: String, domain: String): JSONObject {
        return handleRequestRegistration(linuxUsername, domain)
    }

    private fun dispatch(request: JSONObject, linuxUsername: String): JSONObject {
        return when (request.optString("type")) {
            "VIDEO_LOG" -> handleVideoLog(
                linuxUsername,
                request.optString("platform", "youtube"),
                request.opt("logs"),
            )
            "YOUTUBE_LOG" -> handleVideoLog(linuxUsername, "youtube", request.opt("logs"))
            "TIKTOK_LOG" -> handleVideoLog(linuxUsername, "tiktok", request.opt("logs"))
            "BROWSER_LOG" -> handleBrowserLog(linuxUsername, request.opt("logs"))
            "CHECK_REGISTRATION" -> handleCheckRegistration(linuxUsername, request.optString("domain"))
            "REQUEST_REGISTRATION" -> handleRequestRegistration(linuxUsername, request.optString("domain"))
            "LOGIN_DETECTED" -> handleLoginDetected(
                linuxUsername,
                request.optString("domain"),
                request.optString("username"),
            )
            "ACCESS_REQUEST" -> handleAccessRequest(
                linuxUsername,
                request.optString("reason"),
                request.optString("message"),
            )
            "DIALOGUE_FLAG", "SENTIMENT_BREACH" -> handleDialogueAlert(
                linuxUsername,
                request.optString("type").lowercase(),
                request.optString("platform", "unknown"),
                request.optJSONObject("details") ?: JSONObject(),
            )
            "CHECK_AI_POLICY" -> handleCheckAiPolicy(linuxUsername, request.optString("domain"))
            "CHECK_AI_PROMPT" -> handleCheckAiPrompt(
                linuxUsername,
                request.optString("service"),
                request.optString("domain"),
                request.optString("prompt_text"),
                request.optString("url"),
                request.optString("title"),
            )
            "AI_SESSION_LOG" -> handleAiSessionLog(
                linuxUsername,
                request.optString("domain"),
                request.optInt("duration_seconds"),
            )
            else -> JSONObject().put("success", false).put("message", "Unknown IPC message type")
        }
    }

    private fun handleVideoLog(linuxUsername: String, platform: String, logsRaw: Any?): JSONObject {
        val logs = normalizeLogs(logsRaw)
        if (logs.length() == 0) {
            return JSONObject().put("success", false).put("message", "No logs provided")
        }
        val body = JSONObject()
            .put("linux_username", linuxUsername)
            .put("platform", platform)
            .put("logs", logs)
        return postOrQueueVideo(linuxUsername, platform, body, logs)
    }

    private fun handleBrowserLog(linuxUsername: String, logsRaw: Any?): JSONObject {
        val logs = normalizeLogs(logsRaw)
        if (logs.length() == 0) {
            return JSONObject().put("success", false).put("message", "No logs provided")
        }
        val body = JSONObject()
            .put("linux_username", linuxUsername)
            .put("logs", logs)
        return postOrQueueBrowser(linuxUsername, body, logs)
    }

    private fun handleCheckRegistration(linuxUsername: String, domain: String): JSONObject {
        if (domain.isBlank()) {
            return JSONObject().put("success", false).put("message", "Missing domain")
        }
        val config = configStore.load()
        val body = JSONObject()
            .put("linux_username", linuxUsername)
            .put("domain", domain)
        val result = AgentRestClient.postJson(config, "/api/registration/check", body)
        if (!result.success && result.statusCode == 0) {
            return JSONObject().put("success", true).put("allowed", true)
        }
        return result.body
    }

    private fun handleRequestRegistration(linuxUsername: String, domain: String): JSONObject {
        if (domain.isBlank()) {
            return JSONObject().put("success", false).put("message", "Missing domain")
        }
        val config = configStore.load()
        val body = JSONObject()
            .put("linux_username", linuxUsername)
            .put("domain", domain)
        val result = AgentRestClient.postJson(config, "/api/registration/request", body)
        if (!result.success && result.statusCode == 0) {
            return JSONObject().put("success", false).put("message", "Offline; try again when connected")
        }
        return result.body
    }

    private fun handleLoginDetected(linuxUsername: String, domain: String, loginUsername: String): JSONObject {
        if (domain.isBlank()) {
            return JSONObject().put("success", false).put("message", "Missing domain")
        }
        val config = configStore.load()
        val body = JSONObject()
            .put("linux_username", linuxUsername)
            .put("domain", domain)
            .put("username", loginUsername)
        val result = AgentRestClient.postJson(config, "/api/registration/log-login", body)
        if (!result.success && result.statusCode == 0) {
            return JSONObject().put("success", false).put("message", "Offline; login not logged")
        }
        return result.body
    }

    private fun handleAccessRequest(linuxUsername: String, reason: String, message: String): JSONObject {
        val config = configStore.load()
        val systemId = config.systemId.trim()
        if (systemId.isEmpty()) {
            AlertEventBus.emit(
                "access_requested",
                linuxUsername,
                JSONObject()
                    .put("request_type", "guardian_overlay")
                    .put("reason", reason)
                    .put("message", message),
            )
            return JSONObject().put("success", true).put("message", "Access request queued")
        }
        val body = JSONObject()
            .put("system_id", systemId)
            .put("linux_username", linuxUsername)
            .put("reason", reason)
            .put("message", message)
        val result = AgentRestClient.postJson(config, "/api/access-request", body)
        if (!result.success && result.statusCode == 0) {
            AlertEventBus.emit(
                "access_requested",
                linuxUsername,
                JSONObject()
                    .put("request_type", "guardian_overlay")
                    .put("reason", reason)
                    .put("message", message),
            )
            return JSONObject().put("success", true).put("message", "Access request queued offline")
        }
        return result.body
    }

    private fun handleDialogueAlert(
        linuxUsername: String,
        eventType: String,
        platform: String,
        details: JSONObject,
    ): JSONObject {
        if (!details.has("platform")) {
            details.put("platform", platform)
        }
        AlertEventBus.emit(eventType, linuxUsername, details)
        return JSONObject().put("success", true).put("message", "Alert queued")
    }

    private fun handleCheckAiPolicy(linuxUsername: String, domain: String): JSONObject {
        if (domain.isBlank()) {
            return JSONObject().put("success", false).put("message", "Missing domain")
        }
        val config = configStore.load()
        val body = JSONObject()
            .put("linux_username", linuxUsername)
            .put("domain", domain)
        val result = AgentRestClient.postJson(config, "/api/ai/check-policy", body)
        if (!result.success && result.statusCode == 0) {
            return JSONObject().put("success", true).put("allowed", true)
        }
        return result.body
    }

    private fun handleCheckAiPrompt(
        linuxUsername: String,
        service: String,
        domain: String,
        promptText: String,
        url: String,
        title: String,
    ): JSONObject {
        val config = configStore.load()
        val body = JSONObject()
            .put("linux_username", linuxUsername)
            .put("service", service)
            .put("domain", domain)
            .put("prompt_text", promptText)
            .put("url", url)
            .put("title", title)
        val result = AgentRestClient.postJson(config, "/api/ai/check-prompt", body)
        if (!result.success && result.statusCode == 0) {
            return JSONObject().put("success", true).put("allowed", true)
        }
        return result.body
    }

    private fun handleAiSessionLog(linuxUsername: String, domain: String, durationSeconds: Int): JSONObject {
        val config = configStore.load()
        val body = JSONObject()
            .put("linux_username", linuxUsername)
            .put("domain", domain)
            .put("duration_seconds", durationSeconds)
        val result = AgentRestClient.postJson(config, "/api/ai/log-session", body)
        if (!result.success && result.statusCode == 0) {
            return JSONObject().put("success", false).put("message", "Offline; session not logged")
        }
        return result.body
    }

    private fun postOrQueueBrowser(
        linuxUsername: String,
        body: JSONObject,
        logs: JSONArray,
    ): JSONObject {
        val config = configStore.load()
        val result = AgentRestClient.postJson(config, "/api/browser/log", body)
        if (result.success) {
            return result.body
        }
        for (index in 0 until logs.length()) {
            enqueueBrowserLog(linuxUsername, logs.getJSONObject(index))
        }
        return JSONObject().put("success", false).put("message", "Buffered offline")
    }

    private fun postOrQueueVideo(
        linuxUsername: String,
        platform: String,
        body: JSONObject,
        logs: JSONArray,
    ): JSONObject {
        val config = configStore.load()
        val result = AgentRestClient.postJson(config, "/api/video/log", body)
        if (result.success) {
            return result.body
        }
        for (index in 0 until logs.length()) {
            enqueueVideoLog(linuxUsername, platform, logs.getJSONObject(index))
        }
        return JSONObject().put("success", false).put("message", "Buffered offline")
    }

    private fun enqueueBrowserLog(linuxUsername: String, logEntry: JSONObject) {
        queue.appendBrowserLog(linuxUsername, logEntry)
        scheduleFlush()
    }

    private fun enqueueVideoLog(linuxUsername: String, platform: String, logEntry: JSONObject) {
        queue.appendVideoLog(linuxUsername, platform, logEntry)
        scheduleFlush()
    }

    private fun scheduleFlush() {
        TelemetryFlushWorker.enqueue(context.applicationContext)
    }

    private fun requeue(entries: List<TelemetryQueue.QueuedEntry>) {
        for (entry in entries) {
            when (entry.kind) {
                TelemetryQueue.KIND_BROWSER -> {
                    val logs = entry.payload.optJSONArray("logs") ?: JSONArray()
                    for (index in 0 until logs.length()) {
                        queue.appendBrowserLog(entry.linuxUsername, logs.getJSONObject(index))
                    }
                }
                TelemetryQueue.KIND_VIDEO -> {
                    val platform = entry.payload.optString("platform", "youtube")
                    val logs = entry.payload.optJSONArray("logs") ?: JSONArray()
                    for (index in 0 until logs.length()) {
                        queue.appendVideoLog(entry.linuxUsername, platform, logs.getJSONObject(index))
                    }
                }
            }
        }
    }

    private fun normalizeLogs(logsRaw: Any?): JSONArray {
        return when (logsRaw) {
            is JSONArray -> logsRaw
            is JSONObject -> JSONArray().put(logsRaw)
            else -> JSONArray()
        }
    }

    private data class QueuedEntryRef(val entry: TelemetryQueue.QueuedEntry)

    companion object {
        fun from(context: Context): AgentTelemetryRouter {
            return GuardianApplication.from(context).telemetryRouter
        }

        /** Classify in-browser URLs the same way the Chrome extension background script does. */
        fun classifyBrowserUrl(url: String): BrowserUrlKind {
            val normalized = url.trim()
            val lower = normalized.lowercase()
            return when {
                lower.contains("youtube.com") && (isYoutubeWatchUrl(lower) || lower.contains("/shorts")) ->
                    BrowserUrlKind.YoutubeVideo
                lower.contains("tiktok.com") && lower.contains("/video") ->
                    BrowserUrlKind.TiktokVideo
                else -> BrowserUrlKind.GeneralWeb
            }
        }

        private fun isYoutubeWatchUrl(lowerUrl: String): Boolean {
            return lowerUrl.contains("watch?v=") || lowerUrl.contains("youtu.be/")
        }
    }

    enum class BrowserUrlKind {
        GeneralWeb,
        YoutubeVideo,
        TiktokVideo,
    }
}
