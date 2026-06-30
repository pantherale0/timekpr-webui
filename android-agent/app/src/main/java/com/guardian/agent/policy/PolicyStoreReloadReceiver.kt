package com.guardian.agent.policy

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import com.guardian.agent.GuardianApplication
import com.guardian.agent.enforcement.EnforcementController
import com.guardian.agent.notification.PolicyUpdateNotifier
import java.io.File

/** Reloads replicated policy stores on a managed secondary user. */
class PolicyStoreReloadReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != ACTION_RELOAD_STORES) return
        applyPayloadFiles(context, intent)
        pendingContext = context.applicationContext
        handler.removeCallbacks(reconcileRunnable)
        handler.postDelayed(reconcileRunnable, DEBOUNCE_MS)
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

        private val handler = Handler(Looper.getMainLooper())
        private var pendingContext: Context? = null

        private val reconcileRunnable = Runnable {
            val ctx = pendingContext ?: return@Runnable
            val app = GuardianApplication.from(ctx)
            app.timeLimitStore.reloadFromPrefs()
            app.appPolicyStore.restore()
            app.domainPolicyStore.restore()
            app.deviceRestrictionStore.restore()
            EnforcementController(ctx, app.appPolicyStore).reconcileAllUsers()
            PolicyUpdateNotifier.schedule(ctx)
        }
    }
}
