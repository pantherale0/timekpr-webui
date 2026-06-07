package com.timekpr.agent.monitor

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONObject

/** Receives usage/policy alerts forwarded from managed secondary profile processes. */
class UsageAlertForwardReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != UsageAlertForwarder.ACTION) return
        val eventType = intent.getStringExtra(UsageAlertForwarder.EXTRA_EVENT_TYPE) ?: return
        val linuxUsername = intent.getStringExtra(UsageAlertForwarder.EXTRA_LINUX_USERNAME) ?: return
        val detailsRaw = intent.getStringExtra(UsageAlertForwarder.EXTRA_DETAILS) ?: return
        try {
            AlertEventBus.emit(eventType, linuxUsername, JSONObject(detailsRaw))
        } catch (_: Exception) {
        }
    }
}
