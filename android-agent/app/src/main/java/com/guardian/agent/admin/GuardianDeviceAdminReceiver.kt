package com.guardian.agent.admin

import android.app.admin.DeviceAdminReceiver
import android.content.Context
import android.content.Intent

class GuardianDeviceAdminReceiver : DeviceAdminReceiver() {
    override fun onEnabled(context: Context, intent: Intent) {
        super.onEnabled(context, intent)
        ProvisioningBootstrap.stageFromAdminExtras(
            context,
            ProvisioningBootstrap.readAdminExtras(intent),
        )
        SecondaryUserProvisioner.onSecondaryAdminEnabled(context)
    }

    override fun onDisabled(context: Context, intent: Intent) {
        super.onDisabled(context, intent)
    }
}
