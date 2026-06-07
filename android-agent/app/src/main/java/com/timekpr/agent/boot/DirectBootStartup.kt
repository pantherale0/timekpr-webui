package com.timekpr.agent.boot

import android.content.Context
import android.content.Intent
import android.os.Process
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.admin.SecondaryUserProvisioner
import com.timekpr.agent.enforcement.OwnerProfilePinRotator
import com.timekpr.agent.monitor.UsageMonitorService
import com.timekpr.agent.service.AgentPersistentConnectionService
import com.timekpr.agent.service.AgentSessionCoordinator
import com.timekpr.agent.ui.ParentalAccessActivity
import com.timekpr.agent.util.DirectBootHelper

/** Starts the agent as early as possible (including before credential-encrypted storage unlock). */
object DirectBootStartup {
    fun onBoot(context: Context, action: String, lockedBoot: Boolean) {
        if (Process.myUid() / 100_000 != 0) return

        DeviceOwnerProvisioner.applyIfDeviceOwner(context)
        val config = TimeKprApplication.from(context).configStore.load()
        if (config.serverUrl.isBlank()) return

        AgentSessionCoordinator.scheduleSync(context, reason = if (lockedBoot) "locked_boot" else "boot")
        AgentSessionCoordinator.schedulePeriodicSync(context)
        if (!lockedBoot) {
            UsageMonitorService.start(context)
            AgentPersistentConnectionService.start(context)
        }

        if (DeviceOwnerProvisioner.isDeviceOwner(context)) {
            OwnerProfilePinRotator.refreshPinIfNeeded(context)
            if (lockedBoot && !config.agentToken.isNullOrBlank()) {
                launchLockScreenRecovery(context)
            }
            if (DirectBootHelper.isCredentialStorageUnlocked(context)) {
                SecondaryUserProvisioner.bootstrapAllSecondaryUsers(context)
            }
        }
    }

    private fun launchLockScreenRecovery(context: Context) {
        try {
            val intent = Intent(context, ParentalAccessActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(intent)
        } catch (_: Exception) {
        }
    }
}
