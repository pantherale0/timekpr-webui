package com.guardian.agent.service

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.guardian.agent.config.AgentConfigStore
import com.guardian.agent.util.AndroidUsers
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

class YoutubeAccessibilityService : AccessibilityService() {

    private val handler = Handler(Looper.getMainLooper())
    private var powerManager: PowerManager? = null
    
    private var currentTitle: String? = null
    private var currentChannel: String? = null
    private var accumulatedSeconds = 0
    private var isPlaying = false

    private val monitorRunnable = object : Runnable {
        override fun run() {
            checkActivePlayback()
            handler.postDelayed(this, 1000)
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        Log.i("YoutubeMonitor", "YouTube Accessibility Service Connected")
        powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        handler.post(monitorRunnable)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        // Events are handled periodically by the monitorRunnable loop
        // to ensure accurate active screen duration calculation.
    }

    override fun onInterrupt() {
        flushCurrentLog()
    }

    override fun onDestroy() {
        handler.removeCallbacks(monitorRunnable)
        flushCurrentLog()
        super.onDestroy()
    }

    private fun checkActivePlayback() {
        val pm = powerManager ?: return
        if (!pm.isInteractive) {
            // Screen is off/locked: pause and flush
            if (isPlaying) {
                flushCurrentLog()
                isPlaying = false
            }
            return
        }

        val rootNode = rootInActiveWindow
        if (rootNode == null) {
            // Not in YouTube or window not active: pause and flush
            if (isPlaying) {
                flushCurrentLog()
                isPlaying = false
            }
            return
        }

        val packageName = rootNode.packageName?.toString()
        if (packageName != "com.google.android.youtube") {
            // Switched to another app: pause and flush
            if (isPlaying) {
                flushCurrentLog()
                isPlaying = false
            }
            rootNode.recycle()
            return
        }

        // Search the active view hierarchy for video title and channel name
        val details = VideoDetails()
        findVideoDetails(rootNode, details)
        rootNode.recycle()

        val title = details.title
        val channel = details.channelName ?: "YouTube Creator"

        if (!title.isNullOrBlank()) {
            if (title != currentTitle) {
                // Video changed: flush old video log first
                flushCurrentLog()
                currentTitle = title
                currentChannel = channel
                accumulatedSeconds = 0
            }
            
            // Increment active watch duration
            isPlaying = true
            accumulatedSeconds++

            // Periodically flush logs every 60 seconds of continuous playback
            if (accumulatedSeconds >= 60) {
                flushCurrentLog()
            }
        } else {
            // Title not visible/found (e.g. user in search/home feed): pause and flush
            if (isPlaying) {
                flushCurrentLog()
                isPlaying = false
            }
        }
    }

    private fun flushCurrentLog() {
        val title = currentTitle
        val channel = currentChannel ?: "YouTube Creator"
        val duration = accumulatedSeconds

        if (title != null && duration > 0) {
            uploadWatchLog(title, channel, duration)
        }
        accumulatedSeconds = 0
    }

    private fun findVideoDetails(node: AccessibilityNodeInfo?, details: VideoDetails) {
        if (node == null) return
        val viewId = node.viewIdResourceName
        if (viewId != null) {
            if (viewId.endsWith("title") || viewId.endsWith("video_title")) {
                val text = node.text?.toString()
                if (!text.isNullOrBlank()) {
                    details.title = text
                }
            } else if (viewId.endsWith("channel_name") || viewId.endsWith("channel_title") || viewId.endsWith("channel_link")) {
                val text = node.text?.toString()
                if (!text.isNullOrBlank()) {
                    details.channelName = text
                }
            }
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i)
            findVideoDetails(child, details)
            child?.recycle()
        }
    }

    private fun uploadWatchLog(title: String, channelName: String, durationSeconds: Int) {
        val configStore = AgentConfigStore(applicationContext)
        val config = configStore.load()
        
        val serverUrl = config.serverUrl
        val token = config.agentToken
        val username = AndroidUsers.currentLinuxUsername(applicationContext)

        if (serverUrl.isBlank() || token.isNullOrBlank()) {
            Log.w("YoutubeMonitor", "Skipping upload: Agent is not enrolled or paired yet.")
            return
        }

        Thread {
            try {
                // Convert ws:// or wss:// WebSocket URL to http:// or https:// API URL
                var restUrl = serverUrl.trim().removeSuffix("/ws")
                if (restUrl.startsWith("ws://")) {
                    restUrl = restUrl.replace("ws://", "http://")
                } else if (restUrl.startsWith("wss://")) {
                    restUrl = restUrl.replace("wss://", "https://")
                }

                val url = URL("$restUrl/api/youtube/log")
                val conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json; utf-8")
                conn.setRequestProperty("Authorization", "Bearer $token")
                conn.doOutput = true
                conn.connectTimeout = 10000
                conn.readTimeout = 10000

                val watchedAt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
                    timeZone = TimeZone.getTimeZone("UTC")
                }.format(Date())

                val pseudoId = generatePseudoVideoId(title)
                val jsonPayload = """
                    {
                      "linux_username": "$username",
                      "logs": [
                        {
                          "video_id": "$pseudoId",
                          "title": "${escapeJson(title)}",
                          "channel_name": "${escapeJson(channelName)}",
                          "channel_id": "android_native",
                          "duration_seconds": $durationSeconds,
                          "watched_at": "$watchedAt"
                        }
                      ]
                    }
                """.trimIndent()

                conn.outputStream.use { os ->
                    val input = jsonPayload.toByteArray(charset("utf-8"))
                    os.write(input, 0, input.size)
                }

                val responseCode = conn.responseCode
                if (responseCode == 200) {
                    Log.d("YoutubeMonitor", "Successfully logged YouTube video: $title for $durationSeconds seconds")
                } else {
                    Log.e("YoutubeMonitor", "Failed to log YouTube video. Server returned response code: $responseCode")
                }
                conn.disconnect()
            } catch (e: Exception) {
                Log.e("YoutubeMonitor", "Error uploading YouTube watch log", e)
            }
        }.start()
    }

    private fun generatePseudoVideoId(title: String): String {
        return try {
            val md = MessageDigest.getInstance("MD5")
            val bytes = md.digest(title.toByteArray())
            val hex = bytes.joinToString("") { "%02x".format(it) }
            hex.take(11)
        } catch (e: Exception) {
            "android_nav"
        }
    }

    private fun escapeJson(str: String): String {
        return str.replace("\\", "\\\\")
                  .replace("\"", "\\\"")
                  .replace("\n", "\\n")
                  .replace("\r", "\\r")
                  .replace("\t", "\\t")
    }

    private class VideoDetails {
        var title: String? = null
        var channelName: String? = null
    }
}
