package com.timekpr.agent.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.service.AgentSessionCoordinator

class BootCompletedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != Intent.ACTION_BOOT_COMPLETED) return
        DeviceOwnerProvisioner.applyIfDeviceOwner(context)
        val config = TimeKprApplication.from(context).configStore.load()
        if (config.serverUrl.isBlank()) return
        AgentSessionCoordinator.startMobileAgent(context)
    }
}
