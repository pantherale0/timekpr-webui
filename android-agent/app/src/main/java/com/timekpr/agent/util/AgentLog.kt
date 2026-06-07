package com.timekpr.agent.util

import android.util.Log
import java.util.concurrent.ConcurrentHashMap

/** Rate-limited logging helpers to avoid spamming logcat with expected conditions. */
object AgentLog {
    private val loggedOnce = ConcurrentHashMap.newKeySet<String>()

    fun wOnce(tag: String, key: String, message: String, error: Throwable? = null) {
        if (!loggedOnce.add("$tag:$key")) return
        if (error != null) {
            Log.w(tag, message, error)
        } else {
            Log.w(tag, message)
        }
    }

    fun d(tag: String, message: String) {
        if (Log.isLoggable(tag, Log.DEBUG)) {
            Log.d(tag, message)
        }
    }
}
