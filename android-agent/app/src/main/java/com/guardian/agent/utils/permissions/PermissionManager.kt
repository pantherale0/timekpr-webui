package com.guardian.agent.utils.permissions

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Intent
import android.net.VpnService
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import com.guardian.agent.admin.DeviceAdminActivationActivity
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.admin.GuardianDeviceAdminReceiver

/**
 * Sequentially requests the three core protection privileges during setup.
 * Device Owner provisioning may auto-grant some items without user prompts.
 */
class PermissionManager(
    private val activity: ComponentActivity,
    private val onStateChanged: (PermissionState) -> Unit,
) {
    private var grantInProgress = false

    private val adminComponent by lazy {
        ComponentName(activity, GuardianDeviceAdminReceiver::class.java)
    }

    private val deviceAdminLauncher = activity.registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) {
        continueSequentialGrant()
    }

    private val vpnLauncher = activity.registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) {
        continueSequentialGrant()
    }

    fun refreshState(): PermissionState {
        DeviceOwnerProvisioner.applyIfDeviceOwner(activity)
        return PermissionState(
            deviceAdmin = isDeviceAdminGranted(),
            vpn = DeviceOwnerProvisioner.hasVpnConsent(activity),
            usageAccess = DeviceOwnerProvisioner.hasUsageAccess(activity),
        )
    }

    fun onResume() {
        val state = refreshState()
        onStateChanged(state)
        if (grantInProgress && state.allGranted) {
            grantInProgress = false
        } else if (grantInProgress) {
            continueSequentialGrant()
        }
    }

    fun startSequentialGrant() {
        grantInProgress = true
        continueSequentialGrant()
    }

    private fun continueSequentialGrant() {
        val state = refreshState()
        onStateChanged(state)
        if (state.allGranted) {
            grantInProgress = false
            return
        }
        when {
            !state.deviceAdmin -> requestDeviceAdmin()
            !state.vpn -> requestVpn()
            !state.usageAccess -> requestUsageAccess()
        }
    }

    private fun isDeviceAdminGranted(): Boolean {
        if (DeviceOwnerProvisioner.isDeviceOrProfileOwner(activity)) return true
        return DeviceAdminActivationActivity.isActive(activity)
    }

    private fun requestDeviceAdmin() {
        if (isDeviceAdminGranted()) {
            continueSequentialGrant()
            return
        }
        val intent = Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN).apply {
            putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, adminComponent)
            putExtra(
                DevicePolicyManager.EXTRA_ADD_EXPLANATION,
                "Guardian needs device admin to lock the device when screen time limits are reached and suspend blocked apps.",
            )
        }
        deviceAdminLauncher.launch(intent)
    }

    private fun requestVpn() {
        if (DeviceOwnerProvisioner.hasVpnConsent(activity)) {
            continueSequentialGrant()
            return
        }
        val prepare = VpnService.prepare(activity)
        if (prepare != null) {
            vpnLauncher.launch(prepare)
        } else {
            continueSequentialGrant()
        }
    }

    private fun requestUsageAccess() {
        if (DeviceOwnerProvisioner.hasUsageAccess(activity)) {
            continueSequentialGrant()
            return
        }
        activity.startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
    }
}

data class PermissionState(
    val deviceAdmin: Boolean,
    val vpn: Boolean,
    val usageAccess: Boolean,
) {
    val allGranted: Boolean get() = deviceAdmin && vpn && usageAccess
}
