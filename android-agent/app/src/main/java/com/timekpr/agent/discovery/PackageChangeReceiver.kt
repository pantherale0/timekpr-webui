package com.timekpr.agent.discovery

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.enforcement.EnforcementController
import com.timekpr.agent.service.AgentSessionCoordinator
import com.timekpr.agent.util.AndroidUsers

/**
 * Schedules a sync session when apps are installed, removed, or updated so inventory
 * reaches the server without waiting for the periodic worker.
 */
class PackageChangeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action ?: return
        if (action !in PACKAGE_ACTIONS) {
            return
        }
        val packageName = intent.data?.schemeSpecificPart?.trim().orEmpty()
        if (packageName.isBlank()) {
            return
        }
        if (packageName == context.packageName) {
            if (action == Intent.ACTION_PACKAGE_REPLACED) {
                val pendingResult = goAsync()
                AgentSessionCoordinator.scheduleSync(context.applicationContext, reason = "agent_updated")
                pendingResult.finish()
            }
            return
        }

        val pendingResult = goAsync()

        val appContext = context.applicationContext
        val appPolicyStore = TimeKprApplication.from(appContext).appPolicyStore
        val enforcement = EnforcementController(appContext, appPolicyStore)
        val username = AndroidUsers.currentLinuxUsername(appContext)
        enforcement.applyAppPolicies(username)
        AgentSessionCoordinator.scheduleSync(appContext, reason = "package_changed")
        pendingResult.finish()
    }

    companion object {
        private val PACKAGE_ACTIONS = setOf(
            Intent.ACTION_PACKAGE_ADDED,
            Intent.ACTION_PACKAGE_REMOVED,
            Intent.ACTION_PACKAGE_REPLACED,
        )
    }
}
