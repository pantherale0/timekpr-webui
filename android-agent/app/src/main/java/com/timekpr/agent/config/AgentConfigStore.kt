package com.timekpr.agent.config

import android.content.Context
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
    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun load(): AgentConfig {
        val systemId = prefs.getString(KEY_SYSTEM_ID, null)?.takeIf { it.isNotBlank() }
            ?: UUID.randomUUID().toString().also { saveSystemId(it) }
        return AgentConfig(
            serverUrl = prefs.getString(KEY_SERVER_URL, "") ?: "",
            systemId = systemId,
            registrationToken = prefs.getString(KEY_REGISTRATION_TOKEN, null),
            agentToken = prefs.getString(KEY_AGENT_TOKEN, null),
            agentVersion = prefs.getString(KEY_AGENT_VERSION, DEFAULT_VERSION) ?: DEFAULT_VERSION,
            pairingComplete = prefs.getBoolean(KEY_PAIRING_COMPLETE, false),
        )
    }

    fun saveServerUrl(url: String) {
        prefs.edit().putString(KEY_SERVER_URL, url.trim()).apply()
    }

    fun saveRegistrationToken(token: String?) {
        if (token.isNullOrBlank()) {
            prefs.edit().remove(KEY_REGISTRATION_TOKEN).apply()
        } else {
            prefs.edit().putString(KEY_REGISTRATION_TOKEN, token.trim()).apply()
        }
    }

    fun saveAgentToken(token: String) {
        prefs.edit().putString(KEY_AGENT_TOKEN, token).apply()
    }

    fun savePairingComplete(complete: Boolean) {
        prefs.edit().putBoolean(KEY_PAIRING_COMPLETE, complete).apply()
    }

    fun applyPairingPayload(serverUrl: String, registrationToken: String?) {
        prefs.edit()
            .putString(KEY_SERVER_URL, serverUrl.trim())
            .apply {
                if (registrationToken.isNullOrBlank()) {
                    remove(KEY_REGISTRATION_TOKEN)
                } else {
                    putString(KEY_REGISTRATION_TOKEN, registrationToken.trim())
                }
            }
            .apply()
    }

    private fun saveSystemId(systemId: String) {
        prefs.edit().putString(KEY_SYSTEM_ID, systemId).apply()
    }

    companion object {
        private const val PREFS_NAME = "timekpr_agent_config"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_SYSTEM_ID = "system_id"
        private const val KEY_REGISTRATION_TOKEN = "registration_token"
        private const val KEY_AGENT_TOKEN = "agent_token"
        private const val KEY_AGENT_VERSION = "agent_version"
        private const val KEY_PAIRING_COMPLETE = "pairing_complete"
        private const val DEFAULT_VERSION = "v0.1.0-android"
    }
}
