package com.timekpr.agent.policy

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.enforcement.EnforcementController
import java.io.File

/** Reloads replicated policy stores on a managed secondary user. */
class PolicyStoreReloadReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != ACTION_RELOAD_STORES) return
        applyPayloadFiles(context, intent)
        val app = TimeKprApplication.from(context)
        app.timeLimitStore.reloadFromPrefs()
        app.appPolicyStore.restore()
        app.domainPolicyStore.restore()
        app.deviceRestrictionStore.restore()
        EnforcementController(context, app.appPolicyStore).reconcileAllUsers()
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
        const val ACTION_RELOAD_STORES = "com.timekpr.agent.policy.ACTION_RELOAD_STORES"
        private const val PAYLOAD_PREFIX = "prefs_xml_"
    }
}
