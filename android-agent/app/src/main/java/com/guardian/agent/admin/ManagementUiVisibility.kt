package com.guardian.agent.admin

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.os.Process
import android.util.Log
import com.guardian.agent.GuardianApplication
import com.guardian.agent.ui.MainActivity
import com.guardian.agent.ui.wizard.SetupWizardActivity
import com.guardian.agent.utils.permissions.PermissionState

/**
 * Controls whether the Guardian launcher icon is shown on the primary (owner) profile.
 *
 * After setup completes the icon is hidden so the agent runs headlessly; [MainActivity]
 * stays enabled for notification deep links and setup recovery.
 */
object ManagementUiVisibility {
    private const val TAG = "ManagementUiVisibility"

    fun primaryUserNeedsSetup(context: Context): Boolean {
        if (currentUserId(context) != 0) return false
        if (ProvisioningBootstrap.needsManagementModeSetup(context)) return true

        val config = GuardianApplication.from(context).configStore.load()
        if (config.serverUrl.isBlank()) return true
        return needsPermissionSetup(context)
    }

    fun needsPermissionSetup(context: Context): Boolean {
        DeviceOwnerProvisioner.applyIfDeviceOwner(context)
        if (DeviceOwnerProvisioner.skipsManualPermissionSetup(context)) {
            return false
        }
        val state = PermissionState(
            deviceAdmin = DeviceAdminActivationActivity.isActive(context) ||
                DeviceOwnerProvisioner.isDeviceOrProfileOwner(context),
            vpn = DeviceOwnerProvisioner.hasVpnConsent(context),
            usageAccess = DeviceOwnerProvisioner.hasUsageAccess(context),
        )
        return !state.allGranted
    }

    /** Show or hide the primary-user launcher icon based on current setup state. */
    fun syncPrimaryUserVisibility(context: Context) {
        if (currentUserId(context) != 0) return
        if (primaryUserNeedsSetup(context)) {
            showPrimaryLauncher(context)
        } else {
            hidePrimaryLauncher(context)
        }
    }

    /** Restore the launcher icon after unenroll or other recovery flows. */
    fun restorePrimaryLauncherForRecovery(context: Context) {
        if (currentUserId(context) != 0) return
        showPrimaryLauncher(context)
    }

    private fun showPrimaryLauncher(context: Context) {
        setApplicationHidden(context, hidden = false)
        setManagementActivitiesEnabled(context, enabled = true)
        Log.i(TAG, "Primary launcher visible for user ${currentUserId(context)}")
    }

    private fun hidePrimaryLauncher(context: Context) {
        setApplicationHidden(context, hidden = true)
        ensureMainActivityEnabledForDeepLinks(context)
        Log.i(TAG, "Primary launcher hidden for user ${currentUserId(context)}")
    }

    private fun setApplicationHidden(context: Context, hidden: Boolean) {
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        val admin = ComponentName(context, GuardianDeviceAdminReceiver::class.java)
        if (!dpm.isAdminActive(admin)) return
        try {
            dpm.setApplicationHidden(admin, context.packageName, hidden)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to set application hidden=$hidden for user ${currentUserId(context)}", e)
        }
    }

    private fun ensureMainActivityEnabledForDeepLinks(context: Context) {
        val pm = context.packageManager
        try {
            pm.setComponentEnabledSetting(
                ComponentName(context, MainActivity::class.java),
                PackageManager.COMPONENT_ENABLED_STATE_ENABLED,
                PackageManager.DONT_KILL_APP,
            )
        } catch (e: Exception) {
            Log.w(TAG, "Failed to keep MainActivity enabled for deep links", e)
        }
    }

    private fun setManagementActivitiesEnabled(context: Context, enabled: Boolean) {
        val state = if (enabled) {
            PackageManager.COMPONENT_ENABLED_STATE_ENABLED
        } else {
            PackageManager.COMPONENT_ENABLED_STATE_DISABLED
        }
        val pm = context.packageManager
        for (activityClass in MANAGEMENT_ACTIVITIES) {
            try {
                pm.setComponentEnabledSetting(
                    ComponentName(context, activityClass),
                    state,
                    PackageManager.DONT_KILL_APP,
                )
            } catch (e: Exception) {
                Log.w(TAG, "Failed to toggle ${activityClass.simpleName} for user ${currentUserId(context)}", e)
            }
        }
    }

    private fun currentUserId(context: Context): Int = Process.myUid() / 100_000

    private val MANAGEMENT_ACTIVITIES = listOf(
        MainActivity::class.java,
        SetupWizardActivity::class.java,
    )
}
