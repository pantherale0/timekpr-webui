package com.timekpr.agent.admin

import android.Manifest
import android.app.AppOpsManager
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.net.VpnService
import android.os.Build
import android.util.Log

/**
 * Grants TimeKpr capabilities without user prompts when the app is provisioned as device owner.
 */
object DeviceOwnerProvisioner {
    private const val TAG = "DeviceOwnerProvisioner"

    fun isDeviceOwner(context: Context): Boolean {
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return false
        return dpm.isDeviceOwnerApp(context.packageName)
    }

    /** @return true when this app is device owner (grants may still partially fail). */
    fun applyIfDeviceOwner(context: Context): Boolean {
        if (!isDeviceOwner(context)) return false
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return true
        val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
        val packageName = context.packageName

        grantPermission(dpm, admin, packageName, Manifest.permission.PACKAGE_USAGE_STATS)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            grantPermission(dpm, admin, packageName, Manifest.permission.POST_NOTIFICATIONS)
        }

        try {
            if (dpm.alwaysOnVpnPackage != packageName) {
                dpm.setAlwaysOnVpnPackage(admin, packageName, false)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to set always-on VPN", e)
        }

        return true
    }

    fun hasUsageAccess(context: Context): Boolean {
        val appOps = context.getSystemService(AppOpsManager::class.java) ?: return false
        val mode = appOps.checkOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS,
            android.os.Process.myUid(),
            context.packageName,
        )
        return mode == AppOpsManager.MODE_ALLOWED
    }

    fun hasVpnConsent(context: Context): Boolean = VpnService.prepare(context) == null

    fun hasRequiredCapabilities(context: Context): Boolean {
        return DeviceAdminActivationActivity.isActive(context)
            && hasUsageAccess(context)
            && hasVpnConsent(context)
    }

    private fun grantPermission(
        dpm: DevicePolicyManager,
        admin: ComponentName,
        packageName: String,
        permission: String,
    ) {
        try {
            if (dpm.getPermissionGrantState(admin, packageName, permission)
                != DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED
            ) {
                dpm.setPermissionGrantState(
                    admin,
                    packageName,
                    permission,
                    DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED,
                )
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to grant $permission", e)
        }
    }
}
