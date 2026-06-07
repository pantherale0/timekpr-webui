package com.timekpr.agent.monitor

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Process
import org.json.JSONObject

/** Receives usage alerts from managed profiles and emits them on user 0 (WebSocket host). */
class UsageAlertRelayActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val eventType = intent.getStringExtra(UsageAlertForwarder.EXTRA_EVENT_TYPE) ?: run {
            finish()
            return
        }
        val linuxUsername = intent.getStringExtra(UsageAlertForwarder.EXTRA_LINUX_USERNAME) ?: run {
            finish()
            return
        }
        val detailsRaw = intent.getStringExtra(UsageAlertForwarder.EXTRA_DETAILS) ?: run {
            finish()
            return
        }
        try {
            val details = JSONObject(detailsRaw)
            if (Process.myUid() / 100_000 != 0) {
                val delivered = UsageAlertIpcClient.postAlert(eventType, linuxUsername, details)
                if (!delivered) {
                    ApplicationRestrictionsAlertQueue.append(this, eventType, linuxUsername, details)
                }
            } else {
                AlertEventBus.emit(eventType, linuxUsername, details)
            }
        } catch (_: Exception) {
        }
        finish()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        onCreate(null)
    }
}
