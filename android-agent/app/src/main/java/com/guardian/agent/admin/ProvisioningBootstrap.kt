package com.guardian.agent.admin

import android.app.admin.DevicePolicyManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.PersistableBundle
import android.util.Log
import com.guardian.agent.GuardianApplication
import com.guardian.agent.config.AgentConfigStore
import com.guardian.agent.service.AgentSessionCoordinator

/**
 * Stages and completes server pairing config delivered in Android Enterprise provisioning extras.
 *
 * MDM extras are staged without starting the agent so the setup wizard can continue (Google
 * account, SIM, etc.) and the user can choose a management mode first.
 */
object ProvisioningBootstrap {
    private const val TAG = "ProvisioningBootstrap"

    fun readAdminExtras(intent: Intent): PersistableBundle? {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(
                DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE,
                PersistableBundle::class.java,
            )
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE)
                as? PersistableBundle
        }
    }

    fun stageFromAdminExtras(context: Context, extras: PersistableBundle?): Boolean {
        if (extras == null) {
            return false
        }

        val serverUrl = extras.getString(ProvisioningExtras.SERVER_URL)?.trim().orEmpty()
        if (serverUrl.isEmpty()) {
            return false
        }

        val registrationToken = extras.getString(ProvisioningExtras.REGISTRATION_TOKEN)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }

        GuardianApplication.from(context).configStore.savePendingProvisioningPayload(
            serverUrl,
            registrationToken,
        )
        Log.i(TAG, "Staged MDM provisioning admin extras")
        return true
    }

    fun completeManagementModeSetup(context: Context, managementMode: String) {
        val app = GuardianApplication.from(context)
        app.configStore.completeManagementModeSetup(managementMode)
        DeviceOwnerProvisioner.applyIfDeviceOwner(context)
        if (app.configStore.load().serverUrl.isNotBlank()) {
            AgentSessionCoordinator.startMobileAgent(context)
        }
        Log.i(TAG, "Completed management mode setup: $managementMode")
    }

    fun needsManagementModeSetup(context: Context): Boolean {
        if (!DeviceOwnerProvisioner.isDeviceOwner(context)) {
            return false
        }
        return !GuardianApplication.from(context).configStore.isManagementModeChosen()
    }

    fun isProvisioningComplianceFlow(intent: Intent): Boolean {
        return intent.action == DevicePolicyManager.ACTION_ADMIN_POLICY_COMPLIANCE
    }
}
