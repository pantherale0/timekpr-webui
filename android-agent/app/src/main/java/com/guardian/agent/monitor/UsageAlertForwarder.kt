package com.guardian.agent.monitor

import android.content.Context
import android.content.Intent
import android.os.UserHandle
import org.json.JSONObject

/** Forwards alerts from a managed secondary profile process to user 0 (WebSocket host). */
object UsageAlertForwarder {
    const val ACTION = "com.guardian.agent.monitor.FORWARD_USAGE_ALERT"
    const val RELAY_ACTION = "com.guardian.agent.monitor.RELAY_USAGE_ALERT"
    const val EXTRA_EVENT_TYPE = "event_type"
    const val EXTRA_LINUX_USERNAME = "linux_username"
    const val EXTRA_DETAILS = "details_json"

    fun sendToPrimary(context: Context, eventType: String, linuxUsername: String, details: JSONObject) {
        if (relayViaCrossProfileActivity(context, eventType, linuxUsername, details)) {
            return
        }
        relayViaBroadcast(context, eventType, linuxUsername, details)
    }

    private fun relayViaCrossProfileActivity(
        context: Context,
        eventType: String,
        linuxUsername: String,
        details: JSONObject,
    ): Boolean {
        val intent = Intent(RELAY_ACTION)
            .setClass(context, UsageAlertRelayActivity::class.java)
            .putExtra(EXTRA_EVENT_TYPE, eventType)
            .putExtra(EXTRA_LINUX_USERNAME, linuxUsername)
            .putExtra(EXTRA_DETAILS, details.toString())
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            context.startActivity(intent)
            true
        } catch (_: Exception) {
            false
        }
    }

    private fun relayViaBroadcast(
        context: Context,
        eventType: String,
        linuxUsername: String,
        details: JSONObject,
    ) {
        val intent = Intent(ACTION)
            .setPackage(context.packageName)
            .putExtra(EXTRA_EVENT_TYPE, eventType)
            .putExtra(EXTRA_LINUX_USERNAME, linuxUsername)
            .putExtra(EXTRA_DETAILS, details.toString())
        val user0 = userHandleForId(0) ?: return
        try {
            context.sendBroadcastAsUser(intent, user0)
        } catch (_: Exception) {
        }
    }

    private fun userHandleForId(userId: Int): UserHandle? {
        return try {
            val constructor = UserHandle::class.java.getConstructor(Int::class.javaPrimitiveType)
            constructor.newInstance(userId) as UserHandle
        } catch (_: Exception) {
            null
        }
    }
}
