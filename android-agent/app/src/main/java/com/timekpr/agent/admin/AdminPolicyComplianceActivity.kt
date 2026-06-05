package com.timekpr.agent.admin

import android.app.Activity
import android.app.admin.DevicePolicyManager
import android.os.Build
import android.os.Bundle
import android.os.PersistableBundle
import android.util.Log
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.service.AgentSessionCoordinator

/**
 * Finalizes Android Enterprise QR provisioning by applying server config from admin extras.
 */
class AdminPolicyComplianceActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val extras = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(
                DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE,
                PersistableBundle::class.java,
            )
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE)
                as? PersistableBundle
        }

        if (extras != null) {
            val serverUrl = extras.getString(ProvisioningExtras.SERVER_URL)?.trim().orEmpty()
            val registrationToken = extras.getString(ProvisioningExtras.REGISTRATION_TOKEN)
                ?.trim()
                ?.takeIf { it.isNotEmpty() }

            if (serverUrl.isNotEmpty()) {
                val app = TimeKprApplication.from(this)
                app.configStore.applyPairingPayload(serverUrl, registrationToken)
                DeviceOwnerProvisioner.applyIfDeviceOwner(this)
                AgentSessionCoordinator.startMobileAgent(this)
            } else {
                Log.w(TAG, "Provisioning extras did not include a server URL")
            }
        } else {
            Log.w(TAG, "Provisioning intent did not include admin extras bundle")
        }

        setResult(RESULT_OK)
        finish()
    }

    companion object {
        private const val TAG = "AdminPolicyCompliance"
    }
}
