package com.timekpr.agent.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.timekpr.agent.config.AgentConfigStore
import com.timekpr.agent.enforcement.OwnerProfilePinRotator
import com.timekpr.agent.util.DirectBootHelper

/**
 * Emergency bootstrap for locked boot when credential-encrypted config was never migrated.
 * Shell/adb can seed device-protected config, then reboot to refresh the lock-screen PIN.
 */
class LockedBootRecoveryReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != ACTION) return
        val agentToken = intent.getStringExtra(EXTRA_AGENT_TOKEN)?.trim().orEmpty()
        val serverUrl = intent.getStringExtra(EXTRA_SERVER_URL)?.trim().orEmpty()
        if (agentToken.isBlank() || serverUrl.isBlank()) return

        val store = AgentConfigStore(context.applicationContext)
        store.saveServerUrl(serverUrl)
        store.saveAgentToken(agentToken)
        store.savePairingComplete(true)

        OwnerProfilePinRotator.refreshPinIfNeeded(context.applicationContext)

        if (DirectBootHelper.isCredentialStorageUnlocked(context)) {
            DirectBootStartup.onBoot(context, "recovery_seed", lockedBoot = false)
        }
    }

    companion object {
        const val ACTION = "com.timekpr.agent.LOCKED_BOOT_SEED_CONFIG"
        const val EXTRA_AGENT_TOKEN = "agent_token"
        const val EXTRA_SERVER_URL = "server_url"
    }
}
