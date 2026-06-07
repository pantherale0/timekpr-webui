package com.timekpr.agent.enforcement

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.os.Build
import android.os.UserManager
import android.util.Log
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.admin.TimeKprDeviceAdminReceiver
import com.timekpr.agent.monitor.UsageMonitorService
import com.timekpr.agent.policy.AppPolicyStore
import com.timekpr.agent.policy.DeviceRestrictionPolicy
import com.timekpr.agent.ui.TimeExhaustedOverlay
import com.timekpr.agent.util.AndroidUsers
import com.timekpr.agent.vpn.DomainBlockVpnService
import org.json.JSONArray
import org.json.JSONObject

class EnforcementController(
    private val context: Context,
    private val appPolicyStore: AppPolicyStore,
) {
    private val timeLimitStore = TimeKprApplication.from(context).timeLimitStore
    private val timeExemptionResolver = TimeExemptionResolver(context, timeLimitStore)
    private val adminComponent = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
    private val enforcementPrefs =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val lastTimeExhaustionSuspendedByUser = mutableMapOf<String, Set<String>>()

    init {
        restoreTimeExhaustionSuspended()
    }

    fun startAll() {
        reconcileAllUsers()
    }

    fun reconcileAllUsers() {
        val allUsers = TimeKprApplication.from(context).timeLimitStore.allUsernames()
        allUsers.forEach { username ->
            val uid = getUidForUsername(username)
            applyTimePoliciesForUser(username, uid)
            applyAppPoliciesForUser(username, uid)
            applyDeviceRestrictionsForUser(username, uid)
        }
        DomainBlockVpnService.reconcile(context)
        UsageMonitorService.start(context)
    }

    fun applyTimePolicies(username: String) {
        applyTimePoliciesForUser(username, getUidForUsername(username))
    }

    fun applyTimePoliciesForUser(username: String, uid: Int) {
        val userContext = AndroidUsers.getUserContext(context, uid) ?: context
        val dpm = userContext.getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isAdminActive(adminComponent)) return

        if (!timeLimitStore.isAccessAllowed(username)) {
            enforceTimeExhaustionForUser(username, uid, dpm)
            return
        }
        clearTimeExhaustionForUser(username, uid, dpm)
    }

    fun applyAppPolicies(username: String) {
        applyAppPoliciesForUser(username, getUidForUsername(username))
    }

    fun applyAppPoliciesForUser(username: String, uid: Int) {
        if (!timeLimitStore.isAccessAllowed(username)) {
            applyTimePoliciesForUser(username, uid)
            return
        }

        val userContext = AndroidUsers.getUserContext(context, uid) ?: context
        val dpm = userContext.getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isAdminActive(adminComponent)) return

        val blocked = appPolicyStore.effectiveBlockedPackages(username)
        val previouslyEnforced = appPolicyStore.lastEnforcedBlockedPackages(username)
        val releasedBySync = appPolicyStore.consumePackagesReleasedBySync(username)
        var toUnsuspend = (previouslyEnforced + releasedBySync - blocked).toMutableSet()
        if (blocked.isEmpty() && toUnsuspend.isEmpty()) {
            toUnsuspend.addAll(findSuspendedThirdPartyPackagesForUser(uid))
        }
        val toSuspend = blocked.toTypedArray()
        val toUnsuspendArray = toUnsuspend.toTypedArray()
        setPackagesSuspended(dpm, toUnsuspendArray, false)
        setPackagesSuspended(dpm, toSuspend, true)
        appPolicyStore.setLastEnforcedBlockedPackages(username, blocked)
    }

    private fun enforceTimeExhaustionForUser(username: String, uid: Int, dpm: DevicePolicyManager) {
        val showCallButton = PhoneCallExemption.canMakeCalls(context)
        if (uid == AndroidUsers.activeUserUid(context)) {
            TimeExhaustedOverlay.show(context, showCallButton)
        }

        val exempt = timeExemptionResolver.exemptPackages(username)
        val toSuspend = launcherPackagesForUser(uid) - exempt
        setPackagesSuspended(dpm, toSuspend.toTypedArray(), true)
        lastTimeExhaustionSuspendedByUser[username] = toSuspend
        persistTimeExhaustionSuspended()
    }

    private fun clearTimeExhaustionForUser(username: String, uid: Int, dpm: DevicePolicyManager) {
        if (uid == AndroidUsers.activeUserUid(context)) {
            TimeExhaustedOverlay.dismiss(context)
        }

        val previouslySuspended = lastTimeExhaustionSuspendedByUser.remove(username) ?: emptySet()
        if (previouslySuspended.isNotEmpty()) {
            val stillBlocked = appPolicyStore.effectiveBlockedPackages(username)
            val toUnsuspend = (previouslySuspended - stillBlocked).toTypedArray()
            setPackagesSuspended(dpm, toUnsuspend, false)
        }
        persistTimeExhaustionSuspended()
        applyAppPoliciesForUser(username, uid)
    }

    private fun getUidForUsername(username: String): Int {
        return TimeKprApplication.from(context).timeLimitStore.ensureUser(username, AndroidUsers.currentLinuxUid(context)).linuxUid
    }

    private fun launcherPackagesForUser(uid: Int): Set<String> {
        val userContext = AndroidUsers.getUserContext(context, uid) ?: context
        val packageManager = userContext.packageManager
        return try {
            packageManager.queryIntentActivities(
                Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER),
                PackageManager.MATCH_ALL,
            ).map { it.activityInfo.packageName }.toSet()
        } catch (_: Exception) {
            emptySet()
        }
    }

    private fun findSuspendedThirdPartyPackagesForUser(uid: Int): Set<String> {
        val userContext = AndroidUsers.getUserContext(context, uid) ?: context
        val pm = userContext.packageManager
        return try {
            pm.getInstalledApplications(PackageManager.MATCH_UNINSTALLED_PACKAGES)
                .asSequence()
                .filter { (it.flags and ApplicationInfo.FLAG_SUSPENDED) != 0 }
                .map { it.packageName }
                .filter { it != context.packageName }
                .toSet()
        } catch (_: Exception) {
            emptySet()
        }
    }

    fun applyDeviceRestrictions(username: String) {
        applyDeviceRestrictionsForUser(username, getUidForUsername(username))
    }

    fun applyDeviceRestrictionsForUser(username: String, uid: Int) {
        if (!DeviceOwnerProvisioner.isDeviceOrProfileOwner(context)) return
        val userContext = AndroidUsers.getUserContext(context, uid) ?: context
        val dpm = userContext.getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isAdminActive(adminComponent)) return

        val policy = TimeKprApplication.from(context).deviceRestrictionStore.policyForUser(username)
        applyDeviceRestrictionPolicy(dpm, policy)
    }

    private fun applyDeviceRestrictionPolicy(dpm: DevicePolicyManager, policy: DeviceRestrictionPolicy) {
        try {
            dpm.setShortSupportMessage(adminComponent, policy.shortSupportMessage)
            dpm.setLongSupportMessage(adminComponent, policy.longSupportMessage)
            dpm.setScreenCaptureDisabled(adminComponent, policy.screenCaptureDisabled)
            dpm.setCameraDisabled(adminComponent, policy.cameraDisabled)

            setUserRestriction(dpm, UserManager.DISALLOW_INSTALL_APPS, policy.installAppsDisabled)
            setUserRestriction(dpm, UserManager.DISALLOW_UNINSTALL_APPS, policy.uninstallAppsDisabled)
            setUserRestriction(dpm, UserManager.DISALLOW_FACTORY_RESET, policy.factoryResetDisabled)
            setUserRestriction(dpm, UserManager.DISALLOW_ADJUST_VOLUME, policy.adjustVolumeDisabled)
            setUserRestriction(dpm, UserManager.DISALLOW_MODIFY_ACCOUNTS, policy.modifyAccountsDisabled)
            setUserRestriction(
                dpm,
                UserManager.DISALLOW_MOUNT_PHYSICAL_MEDIA,
                policy.mountPhysicalMediaDisabled,
            )
            setUserRestriction(dpm, UserManager.DISALLOW_BLUETOOTH, policy.bluetoothDisabled)
            setUserRestriction(dpm, UserManager.DISALLOW_OUTGOING_CALLS, policy.outgoingCallsDisabled)
            setUserRestriction(dpm, UserManager.DISALLOW_SMS, policy.smsDisabled)
            setUserRestriction(dpm, UserManager.DISALLOW_UNMUTE_MICROPHONE, policy.microphoneDisabled)
            setUserRestriction(dpm, UserManager.DISALLOW_USB_FILE_TRANSFER, policy.blockUsbFileTransfer)
            setUserRestriction(dpm, UserManager.DISALLOW_WIFI_TETHERING, policy.blockWifiTethering)
            setUserRestriction(dpm, UserManager.DISALLOW_CONFIG_TETHERING, policy.blockWifiTethering)
            setUserRestriction(dpm, UserManager.DISALLOW_NEAR_FIELD_COMMUNICATION_RADIO, policy.blockNfc)

            when {
                policy.developerSettingsDisabled -> {
                    setUserRestriction(dpm, UserManager.DISALLOW_DEBUGGING_FEATURES, true)
                    setUserRestriction(dpm, UserManager.DISALLOW_SAFE_BOOT, true)
                }
                policy.developerSettingsAllowed -> {
                    setUserRestriction(dpm, UserManager.DISALLOW_DEBUGGING_FEATURES, false)
                    setUserRestriction(dpm, UserManager.DISALLOW_SAFE_BOOT, false)
                }
                else -> {
                    setUserRestriction(dpm, UserManager.DISALLOW_DEBUGGING_FEATURES, false)
                    setUserRestriction(dpm, UserManager.DISALLOW_SAFE_BOOT, false)
                }
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                setUserRestriction(dpm, UserManager.DISALLOW_CAMERA_TOGGLE, policy.enforceCameraToggle)
                setUserRestriction(
                    dpm,
                    UserManager.DISALLOW_MICROPHONE_TOGGLE,
                    policy.enforceMicrophoneToggle,
                )
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && dpm.canUsbDataSignalingBeDisabled()) {
                when {
                    policy.blockAllUsbData -> dpm.setUsbDataSignalingEnabled(false)
                    policy.usbDataAccess == DeviceRestrictionPolicy.USB_DATA_ACCESS_ALLOW ||
                        policy.usbDataAccess == DeviceRestrictionPolicy.USB_DATA_ACCESS_UNSPECIFIED ->
                        dpm.setUsbDataSignalingEnabled(true)
                }
            }
        } catch (e: SecurityException) {
            Log.w(TAG, "Failed to apply some device restrictions (requires Device Owner)", e)
        }
    }

    private fun setPackagesSuspended(dpm: DevicePolicyManager, packages: Array<String>, suspended: Boolean) {
        if (packages.isEmpty()) return
        if (!DeviceOwnerProvisioner.isDeviceOrProfileOwner(context)) {
            Log.w(TAG, "Cannot suspend/unsuspend packages: App is not Device Owner or Profile Owner")
            return
        }
        try {
            dpm.setPackagesSuspended(adminComponent, packages, suspended)
        } catch (e: SecurityException) {
            Log.e(TAG, "Failed to set packages suspended (suspended=$suspended) for: ${packages.joinToString()}", e)
        }
    }

    private fun setUserRestriction(dpm: DevicePolicyManager, restriction: String, enabled: Boolean) {
        if (enabled) {
            dpm.addUserRestriction(adminComponent, restriction)
        } else {
            dpm.clearUserRestriction(adminComponent, restriction)
        }
    }

    fun suspendBlockedLaunch(packageName: String, username: String): Boolean {
        if (!timeLimitStore.isAccessAllowed(username)) {
            if (packageName !in timeExemptionResolver.exemptPackages(username)) {
                applyTimePolicies(username)
                return true
            }
            return false
        }

        val blocked = appPolicyStore.effectiveBlockedPackages(username)
        if (packageName !in blocked) {
            return false
        }
        applyAppPolicies(username)
        return true
    }

    private fun persistTimeExhaustionSuspended() {
        val root = JSONObject()
        lastTimeExhaustionSuspendedByUser.forEach { (username, packages) ->
            root.put(username, JSONArray(packages.toList()))
        }
        enforcementPrefs.edit().putString(KEY_TIME_EXHAUSTION_SUSPENDED, root.toString()).apply()
    }

    private fun restoreTimeExhaustionSuspended() {
        val raw = enforcementPrefs.getString(KEY_TIME_EXHAUSTION_SUSPENDED, null) ?: return
        try {
            val root = JSONObject(raw)
            val keys = root.keys()
            while (keys.hasNext()) {
                val username = keys.next()
                val array = root.optJSONArray(username) ?: continue
                val packages = mutableSetOf<String>()
                for (index in 0 until array.length()) {
                    array.optString(index).takeIf { it.isNotBlank() }?.let { packages += it }
                }
                lastTimeExhaustionSuspendedByUser[username] = packages
            }
        } catch (_: Exception) {
            lastTimeExhaustionSuspendedByUser.clear()
        }
    }

    companion object {
        private const val TAG = "EnforcementController"
        private const val PREFS_NAME = "timekpr_enforcement"
        private const val KEY_TIME_EXHAUSTION_SUSPENDED = "time_exhaustion_suspended"
    }
}
