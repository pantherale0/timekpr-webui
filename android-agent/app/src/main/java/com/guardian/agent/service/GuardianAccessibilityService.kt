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
import com.guardian.agent.telemetry.AgentTelemetryRouter
import com.guardian.agent.util.AndroidUsers
import java.security.MessageDigest

class GuardianAccessibilityService : AccessibilityService() {

    private val handler = Handler(Looper.getMainLooper())
    private var powerManager: PowerManager? = null
    private lateinit var telemetryRouter: AgentTelemetryRouter

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
        "com.microsoft.emmx",
    )
    private var lastExtractedUrl: String? = null
    private var lastExtractedTitle: String? = null
    private var stableUrl: String? = null
    private var urlStableSeconds = 0

    private val monitorRunnable = object : Runnable {
        override fun run() {
            checkActivePlayback()
            handler.postDelayed(this, 1000)
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        telemetryRouter = AgentTelemetryRouter.from(applicationContext)
        Log.i(TAG, "Guardian Accessibility Service Connected")
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
            if (isPlaying) {
                flushCurrentLog()
                isPlaying = false
            }
            resetBrowserState()
            return
        }

        val rootNode = rootInActiveWindow
        if (rootNode == null) {
            if (isPlaying) {
                flushCurrentLog()
                isPlaying = false
            }
            resetBrowserState()
            return
        }

        val packageName = rootNode.packageName?.toString()
        if (packageName == "com.google.android.youtube") {
            resetBrowserState()

            val details = VideoDetails()
            findVideoDetails(rootNode, details)
            rootNode.recycle()

            val title = details.title
            val channel = details.channelName ?: "YouTube Creator"

            if (!title.isNullOrBlank()) {
                if (title != currentTitle) {
                    flushCurrentLog()
                    currentTitle = title
                    currentChannel = channel
                    accumulatedSeconds = 0
                }

                isPlaying = true
                accumulatedSeconds++

                if (accumulatedSeconds >= 60) {
                    flushCurrentLog()
                }
            } else if (isPlaying) {
                flushCurrentLog()
                isPlaying = false
            }
        } else if (packageName != null && browserPackages.contains(packageName)) {
            if (isPlaying) {
                flushCurrentLog()
                isPlaying = false
            }

            val url = findBrowserUrl(rootNode)
            val title = findBrowserTitle(rootNode) ?: "Web Page"
            rootNode.recycle()

            processBrowserNavigation(url, title)
        } else {
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
            val username = AndroidUsers.currentLinuxUsername(applicationContext)
            telemetryRouter.queueVideoLog(
                linuxUsername = username,
                platform = "youtube",
                videoId = generatePseudoVideoId(title),
                title = title,
                channelName = channel,
                channelId = "android_native",
                durationSeconds = duration,
            )
            Log.d(TAG, "Queued YouTube watch log: $title ($duration s)")
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

        if (viewId != null && (
                viewId.endsWith("url_bar") ||
                    viewId.endsWith("url_bar_title") ||
                    viewId.endsWith("url_edit_text") ||
                    viewId.endsWith("location_bar_edit_text")
                )
        ) {
            val text = node.text?.toString()
            if (!text.isNullOrBlank()) {
                return text
            }
        }

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

        if (trimmedUrl != lastExtractedUrl) {
            lastExtractedUrl = trimmedUrl
            lastExtractedTitle = title
            urlStableSeconds = 0
        } else {
            urlStableSeconds++
            if (urlStableSeconds == 2 && trimmedUrl != stableUrl) {
                stableUrl = trimmedUrl
                publishBrowserNavigation(trimmedUrl, title)
            }
        }
    }

    private fun publishBrowserNavigation(urlStr: String, title: String) {
        val fullUrl = normalizeUrl(urlStr)
        val domain = extractDomain(fullUrl) ?: return
        val username = AndroidUsers.currentLinuxUsername(applicationContext)

        when (AgentTelemetryRouter.classifyBrowserUrl(fullUrl)) {
            AgentTelemetryRouter.BrowserUrlKind.YoutubeVideo -> {
                telemetryRouter.queueVideoLog(
                    linuxUsername = username,
                    platform = "youtube",
                    videoId = generatePseudoVideoId(fullUrl),
                    title = title,
                    channelName = "Web Browser",
                    channelId = "android_browser",
                    durationSeconds = 0,
                )
                Log.d(TAG, "Queued in-browser YouTube page: $fullUrl")
            }
            AgentTelemetryRouter.BrowserUrlKind.TiktokVideo -> {
                telemetryRouter.queueVideoLog(
                    linuxUsername = username,
                    platform = "tiktok",
                    videoId = generatePseudoVideoId(fullUrl),
                    title = title,
                    channelName = "Web Browser",
                    channelId = "android_browser",
                    durationSeconds = 0,
                )
                Log.d(TAG, "Queued in-browser TikTok page: $fullUrl")
            }
            AgentTelemetryRouter.BrowserUrlKind.GeneralWeb -> {
                telemetryRouter.queueBrowserLog(
                    linuxUsername = username,
                    url = fullUrl,
                    title = title,
                    domain = domain,
                )
                Log.d(TAG, "Queued web history: $fullUrl")
            }
        }
    }

    private fun resetBrowserState() {
        lastExtractedUrl = null
        lastExtractedTitle = null
        urlStableSeconds = 0
    }

    private fun normalizeUrl(urlStr: String): String {
        val trimmed = urlStr.trim()
        return if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
            trimmed
        } else {
            "https://$trimmed"
        }
    }

    private fun extractDomain(urlStr: String): String? {
        return try {
            val uri = java.net.URI(urlStr)
            val host = uri.host
            if (!host.isNullOrBlank()) {
                if (host.startsWith("www.")) host.substring(4) else host
            } else {
                null
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun generatePseudoVideoId(seed: String): String {
        return try {
            val md = MessageDigest.getInstance("MD5")
            val bytes = md.digest(seed.toByteArray())
            val hex = bytes.joinToString("") { "%02x".format(it) }
            hex.take(11)
        } catch (_: Exception) {
            "android_nav"
        }
    }

    private class VideoDetails {
        var title: String? = null
        var channelName: String? = null
    }

    companion object {
        private const val TAG = "GuardianAccessibility"
    }
}
