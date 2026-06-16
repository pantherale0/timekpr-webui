package com.guardian.agent.service

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.os.Build
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

class GuardianAccessibilityService : AccessibilityService() {

    private val handler = Handler(Looper.getMainLooper())
    private var powerManager: PowerManager? = null
    
    // YouTube monitoring state
    private var currentTitle: String? = null
    private var currentChannel: String? = null
    private var accumulatedSeconds = 0
    private var isPlaying = false

    // Web browsing monitoring state
    private val browserPackages = setOf(
        "com.android.chrome",
        "org.chromium.chrome",
        "org.mozilla.firefox",
        "com.sec.android.app.sbrowser",
        "com.brave.browser",
        "com.opera.browser",
        "com.microsoft.emmx"
    )
    private var lastExtractedUrl: String? = null
    private var lastExtractedTitle: String? = null
    private var stableUrl: String? = null
    private var stableTitle: String? = null
    private var urlStableSeconds = 0

    private val monitorRunnable = object : Runnable {
        override fun run() {
            checkActivePlayback()
            handler.postDelayed(this, 1000)
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        Log.i("GuardianAccessibility", "Guardian Accessibility Service Connected")
        powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        handler.post(monitorRunnable)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        // Events are handled periodically by the monitorRunnable loop
        // to ensure accurate active screen duration calculation and debouncing.
    }

    override fun onInterrupt() {
        flushCurrentLog()
        resetBrowserState()
    }

    override fun onDestroy() {
        handler.removeCallbacks(monitorRunnable)
        flushCurrentLog()
        resetBrowserState()
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
            resetBrowserState()
            return
        }

        val rootNode = rootInActiveWindow
        if (rootNode == null) {
            // Window not active: pause and flush
            if (isPlaying) {
                flushCurrentLog()
                isPlaying = false
            }
            resetBrowserState()
            return
        }

        val packageName = rootNode.packageName?.toString()
        if (packageName == "com.google.android.youtube") {
            // Switched to YouTube, reset browser state
            resetBrowserState()

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
                // Title not visible/found: pause and flush
                if (isPlaying) {
                    flushCurrentLog()
                    isPlaying = false
                }
            }
        } else if (packageName != null && browserPackages.contains(packageName)) {
            // In browser, reset YouTube state
            if (isPlaying) {
                flushCurrentLog()
                isPlaying = false
            }

            // Crawl layout to find URL and Title
            val url = findBrowserUrl(rootNode)
            val title = findBrowserTitle(rootNode) ?: "Web Page"
            rootNode.recycle()

            processBrowserNavigation(url, title)
        } else {
            // In other app, reset all states
            if (isPlaying) {
                flushCurrentLog()
                isPlaying = false
            }
            resetBrowserState()
            rootNode.recycle()
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

    private fun findBrowserUrl(node: AccessibilityNodeInfo?): String? {
        if (node == null) return null
        val className = node.className?.toString()
        val viewId = node.viewIdResourceName

        // Direct matching on known address-bar view IDs
        if (viewId != null && (
            viewId.endsWith("url_bar") || 
            viewId.endsWith("url_bar_title") || 
            viewId.endsWith("url_edit_text") || 
            viewId.endsWith("location_bar_edit_text")
        )) {
            val text = node.text?.toString()
            if (!text.isNullOrBlank()) {
                return text
            }
        }

        // Fallback: Check if it's an EditText and looks like a URL/domain (no spaces, contains a dot)
        if (className == "android.widget.EditText" || className == "android.widget.AutoCompleteTextView") {
            val text = node.text?.toString()
            if (!text.isNullOrBlank()) {
                val trimmed = text.trim()
                if (trimmed.contains(".") && !trimmed.contains(" ") && !trimmed.startsWith("http://localhost")) {
                    return trimmed
                }
            }
        }

        for (i in 0 until node.childCount) {
            val child = node.getChild(i)
            val res = findBrowserUrl(child)
            child?.recycle()
            if (res != null) return res
        }
        return null
    }

    private fun findBrowserTitle(node: AccessibilityNodeInfo?): String? {
        if (node == null) return null
        val window = node.window
        if (window != null) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                val title = window.title?.toString()
                if (!title.isNullOrBlank()) {
                    window.recycle()
                    return title
                }
            }
            window.recycle()
        }
        return null
    }

    private fun processBrowserNavigation(url: String?, title: String) {
        if (url.isNullOrBlank()) {
            resetBrowserState()
            return
        }

        val trimmedUrl = url.trim()
        if (!trimmedUrl.contains(".") || trimmedUrl.contains(" ")) {
            resetBrowserState()
            return
        }

        // If the URL changed, reset stable counter
        if (trimmedUrl != lastExtractedUrl) {
            lastExtractedUrl = trimmedUrl
            lastExtractedTitle = title
            urlStableSeconds = 0
        } else {
            urlStableSeconds++
            if (urlStableSeconds == 2) {
                if (trimmedUrl != stableUrl) {
                    stableUrl = trimmedUrl
                    stableTitle = title
                    val domain = extractDomain(trimmedUrl)
                    if (domain != null) {
                        uploadWebHistory(trimmedUrl, title, domain)
                    }
                }
            }
        }
    }

    private fun resetBrowserState() {
        lastExtractedUrl = null
        lastExtractedTitle = null
        urlStableSeconds = 0
    }

    private fun extractDomain(urlStr: String): String? {
        return try {
            var cleanUrl = urlStr.trim()
            if (!cleanUrl.startsWith("http://") && !cleanUrl.startsWith("https://")) {
                cleanUrl = "https://$cleanUrl"
            }
            val uri = java.net.URI(cleanUrl)
            val host = uri.host
            if (!host.isNullOrBlank()) {
                if (host.startsWith("www.")) host.substring(4) else host
            } else {
                val mainPart = cleanUrl.substringAfter("://").substringBefore("/")
                if (mainPart.startsWith("www.")) mainPart.substring(4) else mainPart
            }
        } catch (e: Exception) {
            null
        }
    }

    private fun uploadWebHistory(urlStr: String, title: String, domain: String) {
        val configStore = AgentConfigStore(applicationContext)
        val config = configStore.load()
        
        val serverUrl = config.serverUrl
        val token = config.agentToken
        val username = AndroidUsers.currentLinuxUsername(applicationContext)

        if (serverUrl.isBlank() || token.isNullOrBlank()) {
            Log.w("GuardianAccessibility", "Skipping web history upload: Agent is not enrolled or paired yet.")
            return
        }

        Thread {
            try {
                var restUrl = serverUrl.trim().removeSuffix("/ws")
                if (restUrl.startsWith("ws://")) {
                    restUrl = restUrl.replace("ws://", "http://")
                } else if (restUrl.startsWith("wss://")) {
                    restUrl = restUrl.replace("wss://", "https://")
                }

                val url = URL("$restUrl/api/browser/log")
                val conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json; utf-8")
                conn.setRequestProperty("Authorization", "Bearer $token")
                conn.doOutput = true
                conn.connectTimeout = 10000
                conn.readTimeout = 10000

                val visitedAt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
                    timeZone = TimeZone.getTimeZone("UTC")
                }.format(Date())

                var fullUrl = urlStr
                if (!fullUrl.startsWith("http://") && !fullUrl.startsWith("https://")) {
                    fullUrl = "https://$fullUrl"
                }

                val jsonPayload = """
                    {
                      "linux_username": "$username",
                      "logs": [
                        {
                          "url": "${escapeJson(fullUrl)}",
                          "title": "${escapeJson(title)}",
                          "domain": "${escapeJson(domain)}",
                          "visited_at": "$visitedAt"
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
                    Log.d("GuardianAccessibility", "Successfully logged web page: $fullUrl")
                } else {
                    Log.e("GuardianAccessibility", "Failed to log web page. Server returned: $responseCode")
                }
                conn.disconnect()
            } catch (e: Exception) {
                Log.e("GuardianAccessibility", "Error uploading web history log", e)
            }
        }.start()
    }

    private fun uploadWatchLog(title: String, channelName: String, durationSeconds: Int) {
        val configStore = AgentConfigStore(applicationContext)
        val config = configStore.load()
        
        val serverUrl = config.serverUrl
        val token = config.agentToken
        val username = AndroidUsers.currentLinuxUsername(applicationContext)

        if (serverUrl.isBlank() || token.isNullOrBlank()) {
            Log.w("GuardianAccessibility", "Skipping upload: Agent is not enrolled or paired yet.")
            return
        }

        Thread {
            try {
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
                    Log.d("GuardianAccessibility", "Successfully logged YouTube video: $title for $durationSeconds seconds")
                } else {
                    Log.e("GuardianAccessibility", "Failed to log YouTube video. Server returned: $responseCode")
                }
                conn.disconnect()
            } catch (e: Exception) {
                Log.e("GuardianAccessibility", "Error uploading YouTube watch log", e)
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
