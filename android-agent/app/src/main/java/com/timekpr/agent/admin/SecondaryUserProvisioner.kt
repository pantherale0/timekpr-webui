package com.timekpr.agent.admin

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.os.Process
import android.util.Log
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.enforcement.EnforcementController
import com.timekpr.agent.boot.SecondaryUserInitService
import com.timekpr.agent.monitor.UsageMonitorService
import com.timekpr.agent.vpn.DomainBlockVpnService
import com.timekpr.agent.ui.MainActivity
import com.timekpr.agent.ui.PairingSetupActivity
import com.timekpr.agent.ui.QrScanActivity
import com.timekpr.agent.util.AndroidUsers

/**
 * Coordinates Device Owner affiliation between the primary user (User 0) and
 * secondary users created via [DevicePolicyManager.createAndManageUser].
 */
object SecondaryUserProvisioner {
    private const val TAG = "SecondaryUserProvisioner"
    const val AFFILIATION_ID = "com.timekpr.agent.affiliated"

    fun isManagedSecondaryUser(context: Context): Boolean {
        return currentUserId(context) != 0 && isManagedOnThisUser(context)
    }

    fun ensurePrimaryAffiliation(context: Context) {
        if (currentUserId(context) != 0) return
        if (!DeviceOwnerProvisioner.isDeviceOwner(context)) return
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
        if (!dpm.isAdminActive(admin)) return
        try {
            if (dpm.getAffiliationIds(admin).isEmpty()) {
                dpm.setAffiliationIds(admin, setOf(AFFILIATION_ID))
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to set primary affiliation ids", e)
        }
    }

    fun onSecondaryAdminEnabled(context: Context) {
        if (currentUserId(context) == 0) return
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
        if (!dpm.isAdminActive(admin)) return
        try {
            dpm.setAffiliationIds(admin, setOf(AFFILIATION_ID))
            CrossUserStoreSync.replicateFromPrimaryToCurrentUser(context)
            DeviceOwnerProvisioner.applyManagedCapabilities(context)
            hideManagementUi(context)
            Log.i(TAG, "Secondary user admin enabled for user ${currentUserId(context)}")
        } catch (e: Exception) {
            Log.w(TAG, "Failed to configure secondary user admin", e)
        }
    }

    /** Push enrollment/policies to a newly created user and grant capabilities there. */
    fun setupProvisionedUser(primaryContext: Context, userId: Int) {
        if (userId == 0) return
        CrossUserStoreSync.replicateFromPrimaryToUser(primaryContext, userId)
        val userContext = AndroidUsers.getUserContext(primaryContext, userId) ?: return
        DeviceOwnerProvisioner.applyManagedCapabilities(userContext)
        DomainBlockVpnService.reconcile(userContext)
        bootstrapSecondaryUser(primaryContext, userId)
        SecondaryUserInitService.startOnUser(primaryContext, userId)
        hideManagementUi(userContext)
    }

    private fun bootstrapSecondaryUser(primaryContext: Context, userId: Int) {
        if (userId == 0) return
        if (!DeviceOwnerProvisioner.isDeviceOwner(primaryContext)) return
        try {
            val constructor = android.os.UserHandle::class.java.getConstructor(Int::class.javaPrimitiveType)
            val userHandle = constructor.newInstance(userId) as android.os.UserHandle
            val monitorIntent = android.content.Intent(primaryContext, UsageMonitorService::class.java)
            val startMethod = Context::class.java.getMethod(
                "startForegroundServiceAsUser",
                android.content.Intent::class.java,
                android.os.UserHandle::class.java,
            )
            startMethod.invoke(primaryContext, monitorIntent, userHandle)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to bootstrap services for user $userId", e)
        }
    }

    /** Block launcher/settings entry to management UI on managed child profiles. */
    fun hideManagementUi(context: Context) {
        if (!isManagedSecondaryUser(context)) return
        val dpm = context.getSystemService(DevicePolicyManager::class.java)
        val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
        if (dpm != null && dpm.isAdminActive(admin)) {
            try {
                dpm.setApplicationHidden(admin, context.packageName, true)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to hide launcher icon for user ${currentUserId(context)}", e)
            }
        }
        disableManagementActivities(context, disabled = true)
        Log.i(TAG, "Management UI hidden for user ${currentUserId(context)}")
    }

    fun ensurePrimaryUiVisible(context: Context) {
        if (currentUserId(context) != 0) return
        val dpm = context.getSystemService(DevicePolicyManager::class.java)
        val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
        if (dpm != null && dpm.isAdminActive(admin)) {
            try {
                dpm.setApplicationHidden(admin, context.packageName, false)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to show launcher icon on primary user", e)
            }
        }
        disableManagementActivities(context, disabled = false)
    }

    /** Returns true when the activity should close immediately (managed secondary profile). */
    fun blockManagementActivity(context: Context): Boolean {
        if (!isManagedSecondaryUser(context)) return false
        prepareAtLaunch(context)
        hideManagementUi(context)
        return true
    }

    /** Prepare a managed secondary profile at app launch — no server pairing required. */
    fun prepareAtLaunch(context: Context) {
        val appContext = context.applicationContext
        if (!isManagedSecondaryUser(appContext)) return

        CrossUserStoreSync.replicateFromPrimaryToCurrentUser(appContext)
        DeviceOwnerProvisioner.applyManagedCapabilities(appContext)

        val app = TimeKprApplication.from(appContext)
        app.appPolicyStore.restore()
        app.domainPolicyStore.restore()
        app.deviceRestrictionStore.restore()

        UsageMonitorService.start(appContext)
        EnforcementController(appContext, app.appPolicyStore).reconcileAllUsers()
        hideManagementUi(appContext)
        Log.i(TAG, "Prepared managed secondary user ${currentUserId(appContext)} at launch")
    }

    private fun disableManagementActivities(context: Context, disabled: Boolean) {
        val state = if (disabled) {
            PackageManager.COMPONENT_ENABLED_STATE_DISABLED
        } else {
            PackageManager.COMPONENT_ENABLED_STATE_ENABLED
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

    private fun isManagedOnThisUser(context: Context): Boolean {
        if (DeviceOwnerProvisioner.isDeviceOrProfileOwner(context)) return true
        return DeviceAdminActivationActivity.isActive(context)
    }

    private fun currentUserId(context: Context): Int = Process.myUid() / 100_000

    private val MANAGEMENT_ACTIVITIES = listOf(
        MainActivity::class.java,
        PairingSetupActivity::class.java,
        QrScanActivity::class.java,
    )
}
