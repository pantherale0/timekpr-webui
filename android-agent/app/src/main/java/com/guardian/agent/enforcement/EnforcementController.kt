package com.guardian.agent.enforcement

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.os.Build
import android.os.PersistableBundle
import android.os.UserHandle
import android.os.UserManager
import android.provider.Settings
import android.util.Log
import com.guardian.agent.GuardianApplication
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.admin.SecondaryUserProvisioner
import com.guardian.agent.admin.GuardianDeviceAdminReceiver
import com.guardian.agent.boot.SecondaryUserInitService
import com.guardian.agent.monitor.UsageMonitorService
import com.guardian.agent.policy.AppPolicyStore
import com.guardian.agent.policy.DeviceRestrictionPolicy
import com.guardian.agent.policy.PolicyStorePayloadPush
import com.guardian.agent.policy.ProfileProvisioningStore
import com.guardian.agent.ui.TimeExhaustedOverlay
import com.guardian.agent.ui.GuardianOverlayActivity
import com.guardian.agent.integrity.ClockIntegrityStore
import com.guardian.agent.util.AgentLog
import com.guardian.agent.util.AndroidUsers
import com.guardian.agent.vpn.DomainBlockVpnService
import android.os.Process
import com.guardian.agent.config.AgentConfigStore
import org.json.JSONArray
import org.json.JSONObject

class EnforcementController(
    private val context: Context,
    private val appPolicyStore: AppPolicyStore,
) {
    private val timeLimitStore = GuardianApplication.from(context).timeLimitStore
    private val timeExemptionResolver = TimeExemptionResolver(context, timeLimitStore)
    private val adminComponent = ComponentName(context, GuardianDeviceAdminReceiver::class.java)
    private val enforcementPrefs =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val lastTimeExhaustionSuspendedByUser = mutableMapOf<String, Set<String>>()
    private val lastClockTamperSuspendedByUser = mutableMapOf<String, Set<String>>()
    private var lastOwnerLockdownSuspended = emptySet<String>()
    private var ownerLockdownActive = false
    private val reconcileLock = Any()
    @Volatile
    private var lastAppliedDeviceRestrictionPolicyJson: String? = null

    init {
        restoreTimeExhaustionSuspended()
    }

    fun startAll() {
        reconcileAllUsers()
    }

    fun reconcileAllUsers() {
        synchronized(reconcileLock) {
            reconcileAllUsersLocked()
        }
    }

    private fun reconcileAllUsersLocked() {
        val callingUserId = Process.myUid() / 100_000
        val configStore = GuardianApplication.from(context).configStore
        val mode = configStore.load().managementMode
        val allUsers = GuardianApplication.from(context).timeLimitStore.allUsernames()
        val deviceRestrictionsAppliedForUid = mutableSetOf<Int>()
        allUsers.forEach { username ->
            val uid = getUidForUsername(username)
            if (uid < 0) {
                return@forEach
            }
            if (uid != callingUserId) {
                if (callingUserId == 0 && uid > 0) {
                    delegateEnforcementToUser(uid)
                }
                return@forEach
            }
            if (callingUserId == 0 && mode == AgentConfigStore.MANAGEMENT_MODE_SECONDARY_USERS) {
                // In secondary users mode, User 0 is the parent. Skip time and app policies for User 0,
                // but still run applyDeviceRestrictionsForUser to ensure secondary profiles are provisioned.
                applyDeviceRestrictionsForUser(username, uid)
                return@forEach
            }
            applyTimePoliciesForUser(username, uid)
            applyAppPoliciesForUser(username, uid)
            if (deviceRestrictionsAppliedForUid.add(uid)) {
                applyDeviceRestrictionsForUserLocked(username, uid)
            }
        }
        DomainBlockVpnService.reconcile(context)
        if (Process.myUid() / 100_000 == 0 && !UsageMonitorService.isRunning()) {
            UsageMonitorService.start(context)
        }
        if (Process.myUid() / 100_000 == 0 && mode == AgentConfigStore.MANAGEMENT_MODE_EXCLUSIVE_DO) {
            applyOwnerProfileLockdownIfNeeded()
        }
    }

    fun applyOwnerProfileLockdownIfNeeded() {
        if (Process.myUid() / 100_000 != 0) return
        val configStore = GuardianApplication.from(context).configStore
        if (configStore.load().managementMode == AgentConfigStore.MANAGEMENT_MODE_SECONDARY_USERS) return

        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isAdminActive(adminComponent)) return

        val ownerEval = OwnerProfileLockdown.evaluate(context)

        if (!ownerEval.shouldLock) {
            clearOwnerProfileLockdown(dpm)
            return
        }
        enforceOwnerProfileLockdown(dpm)
    }

    fun onOwnerProfileUnlocked() {
        OwnerProfileLockdown.markUnlockedForCurrentOtpWindow(context)
        applyOwnerProfileLockdownIfNeeded()
    }

    fun applyTimePolicies(username: String) {
        applyTimePoliciesForUser(username, getUidForUsername(username))
    }

    fun applyTimePoliciesForUser(username: String, uid: Int) {
        val userContext = AndroidUsers.getUserContext(context, uid) ?: context
        val dpm = userContext.getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isAdminActive(adminComponent)) return

        if (ClockIntegrityStore(context).isTamperActive()) {
            enforceClockTamperForUser(username, uid, dpm)
            return
        }

        val configStore = GuardianApplication.from(context).configStore
        val isParentUser = uid == 0 && configStore.load().managementMode == AgentConfigStore.MANAGEMENT_MODE_SECONDARY_USERS

        if (!timeLimitStore.isAccessAllowed(username) || (!isParentUser && !DeviceOwnerProvisioner.hasUsageAccess(userContext))) {
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

        val callingUserId = Process.myUid() / 100_000
        if (uid != callingUserId) {
            if (callingUserId == 0 && uid > 0) {
                delegateEnforcementToUser(uid)
            }
            return
        }
        if (uid < 0) return

        val userContext = AndroidUsers.getUserContext(context, uid) ?: context
        val dpm = userContext.getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isAdminActive(adminComponent)) return

        val devicePolicy = GuardianApplication.from(context).deviceRestrictionStore.policyForUser(username)
        val blocked = appPolicyStore.effectiveBlockedPackages(username, userContext) +
            BypassHardening.extraBlockedPackages(devicePolicy)
        val previouslyEnforced = appPolicyStore.lastEnforcedBlockedPackages(username)
        val releasedBySync = appPolicyStore.consumePackagesReleasedBySync(username)
        var toUnsuspend = (previouslyEnforced + releasedBySync - blocked).toMutableSet()
        if (blocked.isEmpty() && toUnsuspend.isEmpty() && !ownerLockdownActive) {
            toUnsuspend.addAll(findSuspendedThirdPartyPackagesForUser(uid))
        }
        if (toUnsuspend.isEmpty() && blocked == previouslyEnforced) {
            return
        }
        val toSuspend = (blocked - previouslyEnforced).toTypedArray()
        val toUnsuspendArray = toUnsuspend.toTypedArray()
        setPackagesSuspended(dpm, toUnsuspendArray, false)
        setPackagesSuspended(dpm, toSuspend, true)
        appPolicyStore.setLastEnforcedBlockedPackages(username, blocked)
    }

    private fun enforceTimeExhaustionForUser(username: String, uid: Int, dpm: DevicePolicyManager) {
        val showCallButton = PhoneCallExemption.canMakeCalls(context)
        if (uid == AndroidUsers.activeUserUid(context)) {
            // Launch the Guardian Space full-screen overlay
            try {
                val overlayIntent = GuardianOverlayActivity.buildIntent(
                    context = context.applicationContext,
                    reason = "sleep",
                    ageTier = null,
                    parentNote = null,
                    deviceName = android.os.Build.MODEL,
                    linuxUsername = username,
                )
                context.startActivity(overlayIntent)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to launch GuardianOverlayActivity for $username", e)
            }
            TimeExhaustedOverlay.show(context, showCallButton)
        }

        val exempt = timeExemptionResolver.exemptPackages(username)
        val userContext = AndroidUsers.getUserContext(context, uid) ?: context
        val toSuspend = lockoutPackagesForUser(uid, userContext, exempt)
        setPackagesSuspended(dpm, toSuspend.toTypedArray(), true)
        lastTimeExhaustionSuspendedByUser[username] = toSuspend
        persistTimeExhaustionSuspended()
    }

    fun enforceClockTamperForActiveUser() {
        val activeUid = AndroidUsers.activeUserUid(context)
        val username = AndroidUsers.usernameForUid(
            context,
            activeUid,
            GuardianApplication.from(context).timeLimitStore,
        )
        val userContext = AndroidUsers.getUserContext(context, activeUid) ?: context
        val dpm = userContext.getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isAdminActive(adminComponent)) return
        enforceClockTamperForUser(username, activeUid, dpm)
    }

    fun clearClockTamperForActiveUser() {
        val activeUid = AndroidUsers.activeUserUid(context)
        val username = AndroidUsers.usernameForUid(
            context,
            activeUid,
            GuardianApplication.from(context).timeLimitStore,
        )
        val userContext = AndroidUsers.getUserContext(context, activeUid) ?: context
        val dpm = userContext.getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isAdminActive(adminComponent)) return
        clearClockTamperForUser(username, activeUid, dpm)
    }

    fun onClockTamperOtpUnlocked() {
        ClockIntegrityStore(context).setOtpOverride(true)
        clearClockTamperForActiveUser()
        reconcileAllUsers()
    }

    private fun enforceClockTamperForUser(username: String, uid: Int, dpm: DevicePolicyManager) {
        val showCallButton = PhoneCallExemption.canMakeCalls(context)
        if (uid == AndroidUsers.activeUserUid(context)) {
            try {
                val overlayIntent = GuardianOverlayActivity.buildIntent(
                    context = context.applicationContext,
                    reason = "clock_tamper",
                    ageTier = null,
                    parentNote = null,
                    deviceName = android.os.Build.MODEL,
                    linuxUsername = username,
                )
                context.startActivity(overlayIntent)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to launch GuardianOverlayActivity for clock tamper", e)
            }
            TimeExhaustedOverlay.show(context, showCallButton, TimeExhaustedOverlay.Mode.CLOCK_TAMPER)
        }

        val exempt = timeExemptionResolver.exemptPackages(username)
        val userContext = AndroidUsers.getUserContext(context, uid) ?: context
        val toSuspend = lockoutPackagesForUser(uid, userContext, exempt)
        setPackagesSuspended(dpm, toSuspend.toTypedArray(), true)
        lastClockTamperSuspendedByUser[username] = toSuspend
    }

    private fun clearClockTamperForUser(username: String, uid: Int, dpm: DevicePolicyManager) {
        if (uid == AndroidUsers.activeUserUid(context)) {
            TimeExhaustedOverlay.dismiss(context)
        }
        val previouslySuspended = lastClockTamperSuspendedByUser.remove(username) ?: emptySet()
        if (previouslySuspended.isNotEmpty()) {
            setPackagesSuspended(dpm, previouslySuspended.toTypedArray(), false)
        }
    }

    private fun clearTimeExhaustionForUser(username: String, uid: Int, dpm: DevicePolicyManager) {
        if (uid == AndroidUsers.activeUserUid(context) && !ownerLockdownActive) {
            TimeExhaustedOverlay.dismiss(context)
        }

        val previouslySuspended = lastTimeExhaustionSuspendedByUser.remove(username) ?: emptySet()
        if (previouslySuspended.isNotEmpty()) {
            val userContext = AndroidUsers.getUserContext(context, uid) ?: context
            val stillBlocked = appPolicyStore.effectiveBlockedPackages(username, userContext)
            val toUnsuspend = (previouslySuspended - stillBlocked).toTypedArray()
            setPackagesSuspended(dpm, toUnsuspend, false)
        }
        persistTimeExhaustionSuspended()
    }

    private fun getUidForUsername(username: String): Int {
        timeLimitStore.persistedLinuxUid(username)?.let { return it }
        val hintedUid = ProfileProvisioningStore(context).userIdFor(username)
        val resolvedUid = AndroidUsers.resolveUidForUsername(context, username, hintedUid)
        return timeLimitStore.ensureUser(username, resolvedUid).linuxUid
    }

    private fun delegateEnforcementToUser(targetUserId: Int) {
        PolicyStorePayloadPush.pushToUser(context, targetUserId)
        SecondaryUserInitService.startOnUser(context, targetUserId)
    }

    private fun lockoutPackagesForUser(
        uid: Int,
        userContext: Context,
        exempt: Set<String>,
    ): Set<String> {
        return launcherPackagesForUser(uid) +
            BypassHardening.settingsPackagesForLockout(userContext) -
            exempt
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
        synchronized(reconcileLock) {
            applyDeviceRestrictionsForUserLocked(username, uid)
        }
    }

    private fun applyDeviceRestrictionsForUserLocked(username: String, uid: Int) {
        if (!DeviceOwnerProvisioner.isDeviceOrProfileOwner(context)) return
        val userContext = AndroidUsers.getUserContext(context, uid) ?: context
        val dpm = userContext.getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isAdminActive(adminComponent)) return

        val configStore = GuardianApplication.from(context).configStore
        val mode = configStore.load().managementMode

        val policy = GuardianApplication.from(context).deviceRestrictionStore.policyForUser(username)

        if (uid == 0) {
            if (mode == AgentConfigStore.MANAGEMENT_MODE_SECONDARY_USERS) {
                provisionProfiles(dpm, policy)
                return
            }
        }

        applyDeviceRestrictionPolicy(dpm, policy)
        if (uid == 0) {
            applyOwnerProfileLockdownIfNeeded()
        }
    }

    private fun enforceOwnerProfileLockdown(dpm: DevicePolicyManager) {
        val ownerUid = 0
        if (ownerUid == AndroidUsers.activeUserUid(context)) {
            TimeExhaustedOverlay.show(context, showCallButton = false, mode = TimeExhaustedOverlay.Mode.OWNER_LOCKDOWN)
        }

        val exempt = setOf(context.packageName)
        val toSuspend = launcherPackagesForUser(ownerUid) - exempt
        if (toSuspend != lastOwnerLockdownSuspended) {
            val released = lastOwnerLockdownSuspended - toSuspend
            if (released.isNotEmpty()) {
                setPackagesSuspended(dpm, released.toTypedArray(), false)
            }
            val added = toSuspend - lastOwnerLockdownSuspended
            if (added.isNotEmpty()) {
                setPackagesSuspended(dpm, added.toTypedArray(), true)
            }
            lastOwnerLockdownSuspended = toSuspend
        }
        ownerLockdownActive = true
    }

    private fun clearOwnerProfileLockdown(dpm: DevicePolicyManager) {
        if (!ownerLockdownActive && lastOwnerLockdownSuspended.isEmpty()) {
            return
        }
        if (lastOwnerLockdownSuspended.isNotEmpty()) {
            setPackagesSuspended(dpm, lastOwnerLockdownSuspended.toTypedArray(), false)
        }
        lastOwnerLockdownSuspended = emptySet()
        ownerLockdownActive = false
        if (AndroidUsers.activeUserUid(context) == 0) {
            TimeExhaustedOverlay.dismiss(context)
        }
    }

    private fun provisionProfiles(dpm: DevicePolicyManager, policy: DeviceRestrictionPolicy) {
        if (!DeviceOwnerProvisioner.isDeviceOrProfileOwner(context)) return
        SecondaryUserProvisioner.ensurePrimaryAffiliation(context)

        val provisioningStore = ProfileProvisioningStore(context)
        val activeUserIds = provisioningStore.allProvisionedUserIds().ifEmpty {
            AndroidUsers.linuxUsersPayload(context)
                .mapNotNull { (it["uid"] as? Number)?.toInt() }
                .toSet()
        }
        provisioningStore.prune(activeUserIds)

        val adminExtras = PersistableBundle().apply {
            putString(SecondaryUserProvisioner.AFFILIATION_ID, SecondaryUserProvisioner.AFFILIATION_ID)
        }

        for (profile in policy.profiles) {
            val existsByRegistry = provisioningStore.isProvisioned(profile.username, activeUserIds)
            val existsByReportedName = AndroidUsers.linuxUsersPayload(context).any {
                (it["username"] as? String)?.equals(profile.username, ignoreCase = true) == true
            }
            if (existsByRegistry || existsByReportedName) {
                continue
            }
            try {
                Log.i(TAG, "Provisioning profile on device: username=${profile.username}, type=${profile.profileType}")
                val flags = buildCreateUserFlags(profile.profileType)
                val userHandle = dpm.createAndManageUser(
                    adminComponent,
                    profile.username,
                    adminComponent,
                    adminExtras,
                    flags,
                )
                if (userHandle != null) {
                    val userId = userHandleIdentifier(userHandle)
                    provisioningStore.record(profile.username, userId)
                    Log.i(TAG, "Successfully created user $userHandle (id=$userId)")
                    startProvisionedUserInBackground(dpm, userHandle, userId, profile.profileType)
                    SecondaryUserProvisioner.setupProvisionedUser(context, userId)
                    if (profile.profileType == "restricted") {
                        applyRestrictedProfileDefaults(userHandle)
                    }
                } else {
                    Log.e(TAG, "Failed to create user (returned null)")
                }
            } catch (e: Exception) {
                Log.e(TAG, "Error provisioning profile: ${profile.username}", e)
            }
        }
    }

    private fun buildCreateUserFlags(profileType: String): Int {
        var flags = 0
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            flags = flags or DevicePolicyManager.LEAVE_ALL_SYSTEM_APPS_ENABLED
        }
        // Standard users must run the system setup wizard on first switch; skipping it
        // leaves SystemUI/navigation in a broken state on many OEM builds.
        if (profileType == "restricted" && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            flags = flags or DevicePolicyManager.SKIP_SETUP_WIZARD
        }
        return flags
    }

    private fun startProvisionedUserInBackground(
        dpm: DevicePolicyManager,
        userHandle: UserHandle,
        userId: Int,
        profileType: String,
    ) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return
        try {
            val result = dpm.startUserInBackground(adminComponent, userHandle)
            Log.i(TAG, "startUserInBackground(user=$userId) result=$result")
            if (profileType == "restricted") {
                finalizeUserSetup(userId)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to start provisioned user $userId in background", e)
        }
    }

    private fun finalizeUserSetup(userId: Int) {
        val userContext = AndroidUsers.getUserContext(context, userId) ?: return
        try {
            Settings.Secure.putInt(
                userContext.contentResolver,
                USER_SETUP_COMPLETE,
                1,
            )
        } catch (e: Exception) {
            Log.w(TAG, "Failed to mark user setup complete for user $userId", e)
        }
    }

    private fun applyRestrictedProfileDefaults(userHandle: UserHandle) {
        val userId = userHandleIdentifier(userHandle)
        val userContext = AndroidUsers.getUserContext(context, userId) ?: return
        val dpm = userContext.getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isAdminActive(adminComponent)) return

        val restrictedDefaults = listOf(
            UserManager.DISALLOW_INSTALL_APPS,
            UserManager.DISALLOW_UNINSTALL_APPS,
            UserManager.DISALLOW_APPS_CONTROL,
            UserManager.DISALLOW_MODIFY_ACCOUNTS,
            UserManager.DISALLOW_FACTORY_RESET,
            UserManager.DISALLOW_USB_FILE_TRANSFER,
        )
        restrictedDefaults.forEach { restriction ->
            setUserRestriction(dpm, restriction, true)
        }
    }

    private fun userHandleIdentifier(userHandle: UserHandle): Int {
        return try {
            val method = UserHandle::class.java.getMethod("getIdentifier")
            method.invoke(userHandle) as? Int ?: userHandle.hashCode()
        } catch (_: Exception) {
            userHandle.hashCode()
        }
    }

    private fun applyDeviceRestrictionPolicy(dpm: DevicePolicyManager, policy: DeviceRestrictionPolicy) {
        val policyJson = policy.toJson().toString()
        if (policyJson == lastAppliedDeviceRestrictionPolicyJson) {
            return
        }
        lastAppliedDeviceRestrictionPolicyJson = policyJson

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
                setUserRestriction(dpm, UserManager.DISALLOW_ADD_USER, true)
                setUserRestriction(dpm, UserManager.DISALLOW_REMOVE_USER, true)
                setUserRestriction(dpm, UserManager.DISALLOW_USER_SWITCH, true)
            }
            policy.developerSettingsAllowed -> {
                setUserRestriction(dpm, UserManager.DISALLOW_DEBUGGING_FEATURES, false)
                setUserRestriction(dpm, UserManager.DISALLOW_SAFE_BOOT, false)
                setUserRestriction(dpm, UserManager.DISALLOW_ADD_USER, false)
                setUserRestriction(dpm, UserManager.DISALLOW_REMOVE_USER, false)
                setUserRestriction(dpm, UserManager.DISALLOW_USER_SWITCH, false)
            }
            else -> {
                setUserRestriction(dpm, UserManager.DISALLOW_DEBUGGING_FEATURES, false)
                setUserRestriction(dpm, UserManager.DISALLOW_SAFE_BOOT, false)
                setUserRestriction(dpm, UserManager.DISALLOW_ADD_USER, false)
                setUserRestriction(dpm, UserManager.DISALLOW_REMOVE_USER, false)
                setUserRestriction(dpm, UserManager.DISALLOW_USER_SWITCH, false)
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
            try {
                when {
                    policy.blockAllUsbData -> dpm.setUsbDataSignalingEnabled(false)
                    policy.usbDataAccess == DeviceRestrictionPolicy.USB_DATA_ACCESS_ALLOW ||
                        policy.usbDataAccess == DeviceRestrictionPolicy.USB_DATA_ACCESS_UNSPECIFIED ->
                        dpm.setUsbDataSignalingEnabled(true)
                }
            } catch (_: SecurityException) {
            }
        }

        policy.forceInstalledApps.forEach { app ->
            if (!isPackageInstalled(app.packageName)) {
                Log.i(TAG, "Force-installed app missing, enqueuing installation: ${app.packageName}")
                com.guardian.agent.update.AppInstallWorker.enqueue(
                    context,
                    app.packageName,
                    app.apkUrl,
                    app.sha256Checksum
                )
            }
        }
    }

    private fun isPackageInstalled(packageName: String): Boolean {
        return try {
            context.packageManager.getPackageInfo(packageName, 0)
            true
        } catch (e: PackageManager.NameNotFoundException) {
            false
        }
    }

    private fun setPackagesSuspended(
        dpm: DevicePolicyManager,
        packages: Array<String>,
        suspended: Boolean,
    ) {
        if (packages.isEmpty()) return
        if (!DeviceOwnerProvisioner.isDeviceOrProfileOwner(context)) {
            AgentLog.d(TAG, "Cannot suspend/unsuspend packages: not device/profile owner")
            return
        }
        try {
            dpm.setPackagesSuspended(adminComponent, packages, suspended)
        } catch (e: SecurityException) {
            AgentLog.d(TAG, "Failed to set packages suspended (suspended=$suspended): ${e.message}")
        }
    }

    private fun setUserRestriction(dpm: DevicePolicyManager, restriction: String, enabled: Boolean) {
        try {
            if (enabled) {
                dpm.addUserRestriction(adminComponent, restriction)
            } else {
                dpm.clearUserRestriction(adminComponent, restriction)
            }
        } catch (_: SecurityException) {
        }
    }

    fun suspendBlockedLaunch(packageName: String, username: String): Boolean {
        if (ClockIntegrityStore(context).isTamperActive()) {
            if (packageName !in timeExemptionResolver.exemptPackages(username)) {
                enforceClockTamperForActiveUser()
                return true
            }
            return false
        }

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
        private const val PREFS_NAME = "guardian_enforcement"
        private const val KEY_TIME_EXHAUSTION_SUSPENDED = "time_exhaustion_suspended"
        private const val USER_SETUP_COMPLETE = "user_setup_complete"
    }
}
