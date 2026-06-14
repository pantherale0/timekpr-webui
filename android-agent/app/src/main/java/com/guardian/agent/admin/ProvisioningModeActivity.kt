package com.guardian.agent.admin

import android.app.Activity
import android.app.admin.DevicePolicyManager
import android.content.Intent
import android.os.Build
import android.os.Bundle

/**
 * Handles Android 12+ managed-device provisioning mode selection during QR setup.
 */
class ProvisioningModeActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val allowedModes = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            intent.getIntegerArrayListExtra(
                DevicePolicyManager.EXTRA_PROVISIONING_ALLOWED_PROVISIONING_MODES,
            )
        } else {
            null
        }

        val selectedMode = if (allowedModes.isNullOrEmpty()) {
            DevicePolicyManager.PROVISIONING_MODE_FULLY_MANAGED_DEVICE
        } else if (allowedModes.contains(DevicePolicyManager.PROVISIONING_MODE_FULLY_MANAGED_DEVICE)) {
            DevicePolicyManager.PROVISIONING_MODE_FULLY_MANAGED_DEVICE
        } else {
            allowedModes.first()
        }

        val result = Intent().apply {
            putExtra(DevicePolicyManager.EXTRA_PROVISIONING_MODE, selectedMode)
        }
        setResult(RESULT_OK, result)
        finish()
    }
}
