package com.timekpr.agent.admin

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
import com.timekpr.agent.util.AgentLog
import com.timekpr.agent.util.DirectBootHelper

/**
 * Grants TimeKpr capabilities without user prompts when the app is provisioned as device owner.
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
        val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
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
        grantCrossUserPermissionsIfDeviceOwner(context)
        configureCrossProfileCommunication(context)
        SecondaryUserProvisioner.configureRelayActivityForUser(context)
        lockManagedProfileSettings(context, dpm, admin)
        setupResetPasswordToken(context, dpm, admin)

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
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
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
        val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
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

    fun hasRequiredCapabilities(context: Context): Boolean {
        return DeviceAdminActivationActivity.isActive(context)
            && hasUsageAccess(context)
            && hasVpnConsent(context)
    }

    private fun grantUsageStatsAppOp(context: Context) {
        val appOps = context.getSystemService(AppOpsManager::class.java) ?: return
        try {
            val method = AppOpsManager::class.java.getMethod(
                "setMode",
                String::class.java,
                Int::class.javaPrimitiveType,
                String::class.java,
                Int::class.javaPrimitiveType,
            )
            method.invoke(
                appOps,
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                android.os.Process.myUid(),
                context.packageName,
                AppOpsManager.MODE_ALLOWED,
            )
        } catch (e: Exception) {
            Log.w(TAG, "Failed to grant GET_USAGE_STATS app op", e)
        }
    }

    private fun grantOverlayPermission(context: Context) {
        val appOps = context.getSystemService(AppOpsManager::class.java) ?: return
        try {
            val method = AppOpsManager::class.java.getMethod(
                "setMode",
                String::class.java,
                Int::class.javaPrimitiveType,
                String::class.java,
                Int::class.javaPrimitiveType,
            )
            method.invoke(
                appOps,
                AppOpsManager.OPSTR_SYSTEM_ALERT_WINDOW,
                android.os.Process.myUid(),
                context.packageName,
                AppOpsManager.MODE_ALLOWED,
            )
        } catch (e: Exception) {
            Log.w(TAG, "Failed to grant SYSTEM_ALERT_WINDOW", e)
        }
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

    private const val PREFS_RECOVERY = "timekpr_recovery_config"
    private const val KEY_RESET_TOKEN = "reset_password_token"

    private fun setupResetPasswordToken(context: Context, dpm: DevicePolicyManager, admin: ComponentName) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            try {
                configureNumericPinQuality(dpm, admin)
                if (!dpm.isResetPasswordTokenActive(admin)) {
                    val token = getOrCreateResetPasswordToken(context)
                    if (token != null) {
                        val success = dpm.setResetPasswordToken(admin, token)
                        AgentLog.d(TAG, "setResetPasswordToken result: $success")
                    }
                } else {
                    AgentLog.d(TAG, "Reset password token is already active")
                }
            } catch (e: Exception) {
                Log.w(TAG, "Failed to register reset password token", e)
            }
        }
    }

    fun configureNumericPinQuality(dpm: DevicePolicyManager, admin: ComponentName) {
        // Allow 6-digit numeric PINs. Avoid minimum-* mutators on Android 16+ unless
        // quality is NUMERIC_COMPLEX; NUMERIC alone is sufficient for OTP codes.
        dpm.setPasswordQuality(admin, DevicePolicyManager.PASSWORD_QUALITY_NUMERIC)
        dpm.setPasswordMinimumLength(admin, 6)
    }

    /** Sets the profile lock-screen PIN when the reset-password token is active. */
    fun resetDevicePassword(context: Context, newPin: String): Boolean {
        if (newPin.length != 6 || !newPin.all { it.isDigit() }) return false
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return false
        val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
        if (!isDeviceOrProfileOwner(context) || !dpm.isAdminActive(admin)) return false

        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val token = getOrCreateResetPasswordToken(context) ?: return false
                if (!dpm.isResetPasswordTokenActive(admin)) {
                    dpm.setResetPasswordToken(admin, token)
                }
                configureNumericPinQuality(dpm, admin)
                if (!dpm.isResetPasswordTokenActive(admin)) {
                    Log.w(TAG, "Reset password token is not active yet")
                    return false
                }
                dpm.resetPasswordWithToken(admin, newPin, token, 0)
            } else {
                @Suppress("DEPRECATION")
                dpm.resetPassword(newPin, 0)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to reset device password", e)
            false
        }
    }

    fun getOrCreateResetPasswordToken(context: Context): ByteArray? {
        val prefs = DirectBootHelper.deviceProtectedContext(context)
            .getSharedPreferences(PREFS_RECOVERY, Context.MODE_PRIVATE)
        val storedHex = prefs.getString(KEY_RESET_TOKEN, null)
        if (!storedHex.isNullOrBlank()) {
            return hexToBytes(storedHex)
        }

        val tokenBytes = ByteArray(32)
        java.security.SecureRandom().nextBytes(tokenBytes)
        val hex = tokenBytes.joinToString("") { "%02x".format(it) }
        prefs.edit().putString(KEY_RESET_TOKEN, hex).commit()
        return tokenBytes
    }

    private fun hexToBytes(hex: String): ByteArray {
        val len = hex.length
        val data = ByteArray(len / 2)
        for (i in 0 until len step 2) {
            data[i / 2] = ((Character.digit(hex[i], 16) shl 4) + Character.digit(hex[i + 1], 16)).toByte()
        }
        return data
    }
}
