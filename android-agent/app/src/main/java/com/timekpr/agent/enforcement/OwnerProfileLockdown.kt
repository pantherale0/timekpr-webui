package com.timekpr.agent.enforcement

import android.content.Context
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.policy.ProfileProvisioningStore
import com.timekpr.agent.util.AndroidUsers
import com.timekpr.agent.util.ParentalAccessOtp
/**
 * Evaluates and tracks temporary unlock state for the device-owner profile (uid 0).
 */
object OwnerProfileLockdown {
    private const val PREFS_NAME = "timekpr_owner_lockdown"
    private const val KEY_UNLOCKED_UNTIL = "unlocked_until_ms"
    private val TIME_STEP_MS = ParentalAccessOtp.TIME_STEP_MS

    data class EvalResult(
        val shouldLock: Boolean,
        val ownerUsername: String,
        val ownerUid: Int,
        val hasSecondaryProfiles: Boolean,
        val serverLockOwnerProfile: Boolean,
        val managedProfileUids: List<Int>,
        val temporarilyUnlocked: Boolean,
        val usedLocalFallback: Boolean,
    )

    fun evaluate(context: Context): EvalResult {
        val ownerUid = 0
        val ownerUsername = AndroidUsers.displayNameForUser(context, ownerUid)
            ?: AndroidUsers.currentLinuxUsername(context)
        val policy = TimeKprApplication.from(context).deviceRestrictionStore.policyForUser("")
        val secondaryUids = policy.managedProfileUids.filter { it > 0 }.ifEmpty {
            ProfileProvisioningStore(context).allProvisionedUserIds().filter { it > 0 }
        }
        val hasSecondary = secondaryUids.isNotEmpty()
        val temporarilyUnlocked = isTemporarilyUnlocked(context)
        val usedLocalFallback = !policy.lockOwnerProfile &&
            policy.managedProfileUids.isEmpty() &&
            hasSecondary
        val shouldLock = !temporarilyUnlocked &&
            DeviceOwnerProvisioner.isDeviceOwner(context) &&
            (
                policy.lockOwnerProfile ||
                    (usedLocalFallback && !policy.managedProfileUids.contains(ownerUid))
                )

        return EvalResult(
            shouldLock = shouldLock,
            ownerUsername = ownerUsername,
            ownerUid = ownerUid,
            hasSecondaryProfiles = hasSecondary,
            serverLockOwnerProfile = policy.lockOwnerProfile,
            managedProfileUids = policy.managedProfileUids,
            temporarilyUnlocked = temporarilyUnlocked,
            usedLocalFallback = usedLocalFallback,
        )
    }

    fun isTemporarilyUnlocked(context: Context): Boolean {
        val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        return System.currentTimeMillis() < prefs.getLong(KEY_UNLOCKED_UNTIL, 0L)
    }

    fun markUnlockedForCurrentOtpWindow(context: Context) {
        val timeSlot = System.currentTimeMillis() / TIME_STEP_MS
        val unlockedUntil = (timeSlot + 1) * TIME_STEP_MS
        context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putLong(KEY_UNLOCKED_UNTIL, unlockedUntil)
            .apply()
    }

    fun clearUnlock(context: Context) {
        context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_UNLOCKED_UNTIL)
            .apply()
    }
}
