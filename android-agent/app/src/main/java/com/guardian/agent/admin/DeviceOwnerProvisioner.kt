package com.guardian.agent.admin

import android.Manifest
import android.app.AppOpsManager
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.net.VpnService
import android.os.Build
import android.os.Process
import android.os.UserManager
import android.util.Log
import com.guardian.agent.util.AgentLog
import com.guardian.agent.util.DirectBootHelper
import com.guardian.agent.GuardianApplication
import com.guardian.agent.config.AgentConfigStore

/**
 * Grants Guardian capabilities without user prompts when the app is provisioned as device owner.
 */
object DeviceOwnerProvisioner {
    private const val TAG = "DeviceOwnerProvisioner"

    fun isDeviceOwner(context: Context): Boolean {
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return false
        return dpm.isDeviceOwnerApp(context.packageName)
    }

    fun isProfileOwner(context: Context): Boolean {
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return false
        return dpm.isProfileOwnerApp(context.packageName)
    }

    fun isDeviceOrProfileOwner(context: Context): Boolean {
        return isDeviceOwner(context) || isProfileOwner(context)
    }

    /** @return true when admin/owner grants were attempted for this user profile. */
    fun applyIfDeviceOwner(context: Context): Boolean {
        if (!shouldAttemptManagedGrants(context)) return false
        return applyManagedCapabilities(context)
    }

    fun applyManagedCapabilities(context: Context): Boolean {
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return false
        val admin = ComponentName(context, GuardianDeviceAdminReceiver::class.java)
        if (!dpm.isAdminActive(admin)) return false
        val packageName = context.packageName

        grantPermission(dpm, admin, packageName, Manifest.permission.PACKAGE_USAGE_STATS)
        grantUsageStatsAppOp(context)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            grantPermission(dpm, admin, packageName, Manifest.permission.REQUEST_INSTALL_PACKAGES)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            grantPermission(dpm, admin, packageName, Manifest.permission.POST_NOTIFICATIONS)
        }
        grantOverlayPermission(context)
        grantVpnAuthorization(context)
        grantCrossUserPermissionsIfDeviceOwner(context)
        configureCrossProfileCommunication(context)
        SecondaryUserProvisioner.configureRelayActivityForUser(context)
        lockManagedProfileSettings(context, dpm, admin)
        
        if (dpm.isDeviceOwnerApp(packageName)) {
            try {
                dpm.setLockTaskPackages(admin, arrayOf(packageName))
            } catch (e: Exception) {
                Log.w(TAG, "Failed to set lock task packages", e)
            }
        }

        return true
    }

    private fun lockManagedProfileSettings(
        context: Context,
        dpm: DevicePolicyManager,
        admin: ComponentName,
    ) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            try {
                dpm.setPermissionPolicy(admin, DevicePolicyManager.PERMISSION_POLICY_AUTO_GRANT)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to set permission auto-grant policy", e)
            }
        }

        val userId = Process.myUid() / 100_000
        if (userId == 0) return

        try {
            dpm.addUserRestriction(admin, UserManager.DISALLOW_APPS_CONTROL)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to lock app settings on managed profile", e)
        }
    }

    fun configureCrossProfileCommunication(context: Context) {
        if (!isProfileOwner(context)) return
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return
        val configStore = GuardianApplication.from(context).configStore
        val mode = configStore.load().managementMode
        val um = context.getSystemService(UserManager::class.java)
        val isManaged = um != null && um.isManagedProfile
        if (mode == AgentConfigStore.MANAGEMENT_MODE_EXCLUSIVE_DO || !isManaged) {
            return
        }
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        val admin = ComponentName(context, GuardianDeviceAdminReceiver::class.java)
        try {
            dpm.setCrossProfilePackages(admin, setOf(context.packageName))
            AgentLog.d(TAG, "Configured cross-profile package allowlist")
        } catch (e: SecurityException) {
            AgentLog.d(TAG, "setCrossProfilePackages not authorized for this profile: ${e.message}")
        } catch (e: Exception) {
            Log.w(TAG, "Failed to configure cross-profile packages", e)
        }
    }

    fun grantCrossUserPermissionsIfDeviceOwner(context: Context) {
        if (!isDeviceOwner(context)) return
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        val admin = ComponentName(context, GuardianDeviceAdminReceiver::class.java)
        val packageName = context.packageName
        for (permission in CROSS_USER_PERMISSIONS) {
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
                Log.w(TAG, "Failed to grant cross-user permission $permission", e)
            }
        }
    }

    private val CROSS_USER_PERMISSIONS = listOf(
        "android.permission.INTERACT_ACROSS_USERS",
        "android.permission.INTERACT_ACROSS_USERS_FULL",
    )

    private fun shouldAttemptManagedGrants(context: Context): Boolean {
        return isDeviceOrProfileOwner(context) ||
            DeviceAdminActivationActivity.isActive(context)
    }

    fun hasOverlayPermission(context: Context): Boolean {
        return android.provider.Settings.canDrawOverlays(context)
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

    /**
     * Device/profile owners can pre-authorize the app's VPN without the system consent dialog.
     *
     * [DomainBlockVpnService] is DNS-only: it intercepts UDP/53 on the TUN interface and does
     * not forward other traffic. We configure always-on VPN to grant consent and keep the service
     * persistent, and disable always-on VPN only when the domain block policy is cleared (empty).
     */
    fun grantVpnAuthorization(context: Context): Boolean {
        if (!isDeviceOrProfileOwner(context)) {
            return false
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            return hasVpnConsent(context)
        }
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return false
        val admin = ComponentName(context, GuardianDeviceAdminReceiver::class.java)
        if (!dpm.isAdminActive(admin)) {
            return false
        }
        return try {
            dpm.setAlwaysOnVpnPackage(admin, context.packageName, false)
            hasVpnConsent(context)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to grant VPN authorization", e)
            false
        }
    }

    private fun disableAlwaysOnVpn(dpm: DevicePolicyManager, admin: ComponentName) {
        try {
            dpm.setAlwaysOnVpnPackage(admin, null, false)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to disable always-on VPN", e)
        }
    }

    fun clearVpnAuthorization(context: Context) {
        if (!isDeviceOrProfileOwner(context)) {
            return
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            return
        }
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        val admin = ComponentName(context, GuardianDeviceAdminReceiver::class.java)
        disableAlwaysOnVpn(dpm, admin)
    }

    fun hasRequiredCapabilities(context: Context): Boolean {
        if (isDeviceOrProfileOwner(context)) {
            return hasUsageAccess(context) && hasVpnConsent(context)
        }
        return DeviceAdminActivationActivity.isActive(context)
            && hasUsageAccess(context)
            && hasVpnConsent(context)
    }

    /** Device/profile owner installs manage these silently; skip manual setup wizards. */
    fun skipsManualPermissionSetup(context: Context): Boolean {
        return isDeviceOrProfileOwner(context)
    }

    private fun grantUsageStatsAppOp(context: Context) {
        val appOps = context.getSystemService(AppOpsManager::class.java) ?: return
        val uid = Process.myUid()
        val packageName = context.packageName
        if (setAppOpMode(appOps, AppOpsManager.OPSTR_GET_USAGE_STATS, uid, packageName)) {
            return
        }
        Log.w(TAG, "Usage access app-op grant did not stick")
    }

    private fun grantOverlayPermission(context: Context) {
        val appOps = context.getSystemService(AppOpsManager::class.java) ?: return
        val uid = Process.myUid()
        val packageName = context.packageName
        if (setAppOpMode(appOps, AppOpsManager.OPSTR_SYSTEM_ALERT_WINDOW, uid, packageName)) {
            return
        }
        Log.w(TAG, "Overlay app-op grant did not stick")
    }

    private fun setAppOpMode(
        appOps: AppOpsManager,
        op: String,
        uid: Int,
        packageName: String,
    ): Boolean {
        try {
            val setMode = AppOpsManager::class.java.getMethod(
                "setMode",
                String::class.java,
                Int::class.javaPrimitiveType,
                String::class.java,
                Int::class.javaPrimitiveType,
            )
            setMode.invoke(appOps, op, uid, packageName, AppOpsManager.MODE_ALLOWED)
            if (checkAppOpAllowed(appOps, op, uid, packageName)) {
                return true
            }
        } catch (e: Exception) {
            Log.w(TAG, "setMode failed for $op", e)
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            try {
                val setUidMode = AppOpsManager::class.java.getMethod(
                    "setUidMode",
                    String::class.java,
                    Int::class.javaPrimitiveType,
                    Int::class.javaPrimitiveType,
                )
                setUidMode.invoke(appOps, op, uid, AppOpsManager.MODE_ALLOWED)
                if (checkAppOpAllowed(appOps, op, uid, packageName)) {
                    return true
                }
            } catch (e: Exception) {
                Log.w(TAG, "setUidMode failed for $op", e)
            }
        }
        return false
    }

    private fun checkAppOpAllowed(
        appOps: AppOpsManager,
        op: String,
        uid: Int,
        packageName: String,
    ): Boolean {
        return appOps.checkOpNoThrow(op, uid, packageName) == AppOpsManager.MODE_ALLOWED
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
