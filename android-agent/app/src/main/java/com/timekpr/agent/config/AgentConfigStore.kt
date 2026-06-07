package com.timekpr.agent.config

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import com.timekpr.agent.BuildConfig
import com.timekpr.agent.util.DirectBootHelper
import java.util.UUID

data class AgentConfig(
    val serverUrl: String,
    val systemId: String,
    val registrationToken: String?,
    val agentToken: String?,
    val agentVersion: String,
    val pairingComplete: Boolean,
)

class AgentConfigStore(context: Context) {
    private val appContext = context.applicationContext
    private val storageContext = DirectBootHelper.deviceProtectedContext(appContext)
    private val prefs = storageContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun migrateToDeviceProtectedStorageIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return
        if (prefs.contains(KEY_SERVER_URL)) return
        if (!DirectBootHelper.isCredentialStorageUnlocked(appContext)) return
        val credentialPrefs = try {
            appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        } catch (_: IllegalStateException) {
            return
        }
        if (credentialPrefs.all.isEmpty()) return
        copySharedPreferences(credentialPrefs, prefs)
    }

    fun load(): AgentConfig {
        migrateToDeviceProtectedStorageIfNeeded()
        val systemId = prefs.getString(KEY_SYSTEM_ID, null)?.takeIf { it.isNotBlank() }
            ?: UUID.randomUUID().toString().also { saveSystemId(it) }
        return AgentConfig(
            serverUrl = prefs.getString(KEY_SERVER_URL, "") ?: "",
            systemId = systemId,
            registrationToken = prefs.getString(KEY_REGISTRATION_TOKEN, null),
            agentToken = prefs.getString(KEY_AGENT_TOKEN, null),
            agentVersion = prefs.getString(KEY_AGENT_VERSION, null)?.takeIf { it.isNotBlank() }
                ?: BuildConfig.DEFAULT_AGENT_VERSION,
            pairingComplete = prefs.getBoolean(KEY_PAIRING_COMPLETE, false),
        )
    }

    fun saveServerUrl(url: String) {
        writeBoth { putString(KEY_SERVER_URL, url.trim()) }
    }

    fun saveRegistrationToken(token: String?) {
        writeBoth {
            if (token.isNullOrBlank()) {
                remove(KEY_REGISTRATION_TOKEN)
            } else {
                putString(KEY_REGISTRATION_TOKEN, token.trim())
            }
        }
    }

    fun saveAgentToken(token: String) {
        writeBoth { putString(KEY_AGENT_TOKEN, token) }
    }

    fun savePairingComplete(complete: Boolean) {
        writeBoth { putBoolean(KEY_PAIRING_COMPLETE, complete) }
    }

    fun saveFcmToken(token: String) {
        writeBoth { putString(KEY_FCM_TOKEN, token.trim()) }
    }

    fun cachedFcmToken(): String? {
        return prefs.getString(KEY_FCM_TOKEN, null)?.takeIf { it.isNotBlank() }
    }

    fun applyPairingPayload(serverUrl: String, registrationToken: String?) {
        writeBoth {
            putString(KEY_SERVER_URL, serverUrl.trim())
            if (registrationToken.isNullOrBlank()) {
                remove(KEY_REGISTRATION_TOKEN)
            } else {
                putString(KEY_REGISTRATION_TOKEN, registrationToken.trim())
            }
        }
    }

    fun clearEnrollmentState() {
        writeBoth {
            remove(KEY_AGENT_TOKEN)
            putBoolean(KEY_PAIRING_COMPLETE, false)
            remove(KEY_FCM_TOKEN)
        }
    }

    private fun saveSystemId(systemId: String) {
        writeBoth { putString(KEY_SYSTEM_ID, systemId) }
    }

    private fun writeBoth(block: SharedPreferences.Editor.() -> Unit) {
        prefs.edit().apply(block).apply()
        if (DirectBootHelper.isCredentialStorageUnlocked(appContext)) {
            try {
                appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                    .edit()
                    .apply(block)
                    .apply()
            } catch (_: IllegalStateException) {
            }
        }
    }

    private fun copySharedPreferences(from: SharedPreferences, to: SharedPreferences) {
        val editor = to.edit().clear()
        for ((key, value) in from.all) {
            when (value) {
                is String -> editor.putString(key, value)
                is Boolean -> editor.putBoolean(key, value)
                is Int -> editor.putInt(key, value)
                is Long -> editor.putLong(key, value)
                is Float -> editor.putFloat(key, value)
                is Set<*> -> {
                    @Suppress("UNCHECKED_CAST")
                    editor.putStringSet(key, value as Set<String>)
                }
            }
        }
        editor.apply()
    }

    companion object {
        private const val PREFS_NAME = "timekpr_agent_config"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_SYSTEM_ID = "system_id"
        private const val KEY_REGISTRATION_TOKEN = "registration_token"
        private const val KEY_AGENT_TOKEN = "agent_token"
        private const val KEY_AGENT_VERSION = "agent_version"
        private const val KEY_PAIRING_COMPLETE = "pairing_complete"
        private const val KEY_FCM_TOKEN = "fcm_token"
    }
}
