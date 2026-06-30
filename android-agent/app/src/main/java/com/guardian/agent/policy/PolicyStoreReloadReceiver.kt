package com.guardian.agent.policy

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.guardian.agent.enforcement.EnforcementCoordinator
import java.io.File

/** Reloads replicated policy stores on a managed secondary user. */
class PolicyStoreReloadReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != ACTION_RELOAD_STORES) return
        val pendingResult = goAsync()
        val appContext = context.applicationContext
        applyPayloadFiles(appContext, intent)
        EnforcementCoordinator.schedulePolicyReloadAndReconcileDebounced(appContext, DEBOUNCE_MS)
        pendingResult.finish()
    }

    private fun applyPayloadFiles(context: Context, intent: Intent) {
        val extras = intent.extras ?: return
        val targetDir = File(context.applicationInfo.dataDir, "shared_prefs")
        targetDir.mkdirs()
        for (key in extras.keySet()) {
            if (!key.startsWith(PAYLOAD_PREFIX)) continue
            val prefsName = key.removePrefix(PAYLOAD_PREFIX)
            val xml = extras.getString(key) ?: continue
            File(targetDir, "$prefsName.xml").writeText(xml)
        }
    }

    companion object {
        const val ACTION_RELOAD_STORES = "com.guardian.agent.policy.ACTION_RELOAD_STORES"
        private const val PAYLOAD_PREFIX = "prefs_xml_"
        private const val DEBOUNCE_MS = 400L
    }
}
