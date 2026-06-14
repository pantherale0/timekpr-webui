package com.guardian.agent.monitor

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Handler
import android.os.Looper
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Tracks the foreground package via UsageStatsManager (requires usage access).
 */
object ForegroundAppTracker {
    private const val POLL_INTERVAL_MS = 2_000L
    private const val LOOKBACK_MS = 5_000L

    private val started = AtomicBoolean(false)
    private val handler = Handler(Looper.getMainLooper())
    private var foregroundPackage: String? = null
    private var appContext: Context? = null

    private val pollRunnable = object : Runnable {
        override fun run() {
            if (!started.get()) return
            appContext?.let { pollForeground(it) }
            handler.postDelayed(this, POLL_INTERVAL_MS)
        }
    }

    fun ensureStarted(context: Context) {
        if (!started.compareAndSet(false, true)) return
        appContext = context.applicationContext
        pollForeground(appContext!!)
        handler.postDelayed(pollRunnable, POLL_INTERVAL_MS)
    }

    fun stop() {
        started.set(false)
        handler.removeCallbacks(pollRunnable)
        appContext = null
        foregroundPackage = null
    }

    fun getForegroundPackage(): String? = foregroundPackage

    private fun pollForeground(context: Context) {
        val usageStatsManager = context.getSystemService(UsageStatsManager::class.java) ?: return
        val end = System.currentTimeMillis()
        val start = end - LOOKBACK_MS
        val events = usageStatsManager.queryEvents(start, end)
        val event = UsageEvents.Event()
        var latestPackage: String? = null
        var latestTimestamp = 0L

        while (events.hasNextEvent()) {
            events.getNextEvent(event)
            if (event.eventType != UsageEvents.Event.ACTIVITY_RESUMED) continue
            val packageName = event.packageName ?: continue
            if (event.timeStamp >= latestTimestamp) {
                latestTimestamp = event.timeStamp
                latestPackage = packageName
            }
        }
        foregroundPackage = latestPackage
    }
}
