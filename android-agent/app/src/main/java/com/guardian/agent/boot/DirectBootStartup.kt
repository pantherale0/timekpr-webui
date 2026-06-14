package com.guardian.agent.boot

import android.content.Context
import android.content.Intent
import android.os.Process
import com.guardian.agent.GuardianApplication
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.admin.SecondaryUserProvisioner
import com.guardian.agent.monitor.UsageMonitorService
import com.guardian.agent.service.AgentPersistentConnectionService
import com.guardian.agent.service.AgentSessionCoordinator
import com.guardian.agent.util.DirectBootHelper

/** Starts the agent as early as possible (including before credential-encrypted storage unlock). */
object DirectBootStartup {
    fun onBoot(context: Context, action: String, lockedBoot: Boolean) {
        if (Process.myUid() / 100_000 != 0) return

        DeviceOwnerProvisioner.applyIfDeviceOwner(context)
        val config = GuardianApplication.from(context).configStore.load()
        if (config.serverUrl.isBlank()) return

        AgentSessionCoordinator.scheduleSync(context, reason = if (lockedBoot) "locked_boot" else "boot")
        AgentSessionCoordinator.schedulePeriodicSync(context)
        if (!lockedBoot) {
            UsageMonitorService.start(context)
            AgentPersistentConnectionService.start(context)
        }

        if (DeviceOwnerProvisioner.isDeviceOwner(context)) {
            if (DirectBootHelper.isCredentialStorageUnlocked(context)) {
                SecondaryUserProvisioner.bootstrapAllSecondaryUsers(context)
            }
        }
    }
}
