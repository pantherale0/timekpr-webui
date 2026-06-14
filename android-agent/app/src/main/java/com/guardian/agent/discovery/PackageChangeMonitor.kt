package com.guardian.agent.discovery

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import com.guardian.agent.enforcement.EnforcementController
import com.guardian.agent.service.AgentSessionCoordinator
import com.guardian.agent.util.AndroidUsers

/**
 * Listens for package installs while a foreground service is running. More reliable
 * than manifest-only receivers for Play Store session installs on recent Android versions.
 */
class PackageChangeMonitor(
    private val context: Context,
    private val enforcement: EnforcementController,
) {
    private var registered = false
    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(ctx: Context, intent: Intent?) {
            val action = intent?.action ?: return
            if (action !in PACKAGE_ACTIONS) return
            val packageName = intent.data?.schemeSpecificPart?.trim().orEmpty()
            if (packageName.isBlank() || packageName == ctx.packageName) return

            val username = AndroidUsers.currentLinuxUsername(ctx)
            enforcement.applyAppPolicies(username)
            AgentSessionCoordinator.scheduleSync(ctx.applicationContext, reason = "package_changed")
        }
    }

    fun register() {
        if (registered) return
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_PACKAGE_ADDED)
            addAction(Intent.ACTION_PACKAGE_REMOVED)
            addAction(Intent.ACTION_PACKAGE_REPLACED)
            addDataScheme("package")
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            context.registerReceiver(receiver, filter)
        }
        registered = true
    }

    fun unregister() {
        if (!registered) return
        try {
            context.unregisterReceiver(receiver)
        } catch (_: IllegalArgumentException) {
        }
        registered = false
    }

    companion object {
        private val PACKAGE_ACTIONS = setOf(
            Intent.ACTION_PACKAGE_ADDED,
            Intent.ACTION_PACKAGE_REMOVED,
            Intent.ACTION_PACKAGE_REPLACED,
        )
    }
}
