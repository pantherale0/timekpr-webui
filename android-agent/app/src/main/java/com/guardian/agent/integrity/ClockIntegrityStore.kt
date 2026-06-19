package com.guardian.agent.integrity

import android.content.Context

class ClockIntegrityStore(context: Context) {
    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun loadStateJson(): String = prefs.getString(KEY_STATE_JSON, "") ?: ""

    fun saveStateJson(json: String) {
        prefs.edit().putString(KEY_STATE_JSON, json).apply()
    }

    fun isTamperActive(): Boolean {
        val raw = loadStateJson()
        if (raw.isBlank()) return false
        return try {
            val json = org.json.JSONObject(raw)
            json.optBoolean("tamper_active", false) &&
                !json.optBoolean("otp_override_active", false)
        } catch (_: Exception) {
            false
        }
    }

    fun trustedWallMs(): Long =
        uniffi.guardian_agent.clockIntegrityTrustedWallMs(
            loadStateJson(),
            android.os.SystemClock.elapsedRealtime(),
        )

    fun setOtpOverride(active: Boolean) {
        val updated = uniffi.guardian_agent.clockIntegritySetOtpOverride(loadStateJson(), active)
        saveStateJson(updated)
    }

    companion object {
        private const val PREFS_NAME = "guardian_clock_integrity"
        private const val KEY_STATE_JSON = "state_json"
    }
}
