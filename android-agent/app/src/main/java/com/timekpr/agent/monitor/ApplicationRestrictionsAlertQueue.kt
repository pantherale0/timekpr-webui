package com.timekpr.agent.monitor

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.os.Bundle
import com.timekpr.agent.admin.TimeKprDeviceAdminReceiver
import org.json.JSONArray
import org.json.JSONObject

/** Pending alerts stored in DPM application restrictions (readable across affiliated profiles). */
object ApplicationRestrictionsAlertQueue {
    private const val KEY_PENDING_ALERTS = "pending_alerts"

    fun append(context: Context, eventType: String, linuxUsername: String, details: JSONObject) {
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
        if (!dpm.isAdminActive(admin)) return
        val alerts = loadAlerts(dpm, admin, context.packageName)
        alerts.put(
            JSONObject()
                .put("event_type", eventType)
                .put("linux_username", linuxUsername)
                .put("details", details),
        )
        dpm.setApplicationRestrictions(admin, context.packageName, bundleFor(alerts))
    }

    fun drain(context: Context): List<PendingAlertStore.PendingAlert> {
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return emptyList()
        val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
        if (!dpm.isAdminActive(admin)) return emptyList()
        val alerts = loadAlerts(dpm, admin, context.packageName)
        if (alerts.length() == 0) return emptyList()
        dpm.setApplicationRestrictions(admin, context.packageName, bundleFor(JSONArray()))
        return parseAlerts(alerts)
    }

    private fun loadAlerts(dpm: DevicePolicyManager, admin: ComponentName, packageName: String): JSONArray {
        val raw = dpm.getApplicationRestrictions(admin, packageName)?.getString(KEY_PENDING_ALERTS) ?: return JSONArray()
        return try {
            JSONArray(raw)
        } catch (_: Exception) {
            JSONArray()
        }
    }

    private fun bundleFor(alerts: JSONArray): Bundle {
        return Bundle().apply { putString(KEY_PENDING_ALERTS, alerts.toString()) }
    }

    private fun parseAlerts(alerts: JSONArray): List<PendingAlertStore.PendingAlert> {
        val parsed = mutableListOf<PendingAlertStore.PendingAlert>()
        for (index in 0 until alerts.length()) {
            val json = alerts.optJSONObject(index) ?: continue
            val eventType = json.optString("event_type")
            val username = json.optString("linux_username")
            val details = json.optJSONObject("details") ?: JSONObject()
            if (eventType.isNotBlank() && username.isNotBlank()) {
                parsed.add(PendingAlertStore.PendingAlert(eventType, username, details))
            }
        }
        return parsed
    }
}
