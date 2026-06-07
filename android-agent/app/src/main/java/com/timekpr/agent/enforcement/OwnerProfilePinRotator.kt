package com.timekpr.agent.enforcement

import android.content.Context
import android.os.Process
import android.util.Log
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.util.ParentalAccessOtp

/**
 * Rotates the device-owner profile lock-screen PIN to match the current parental-access OTP.
 */
object OwnerProfilePinRotator {
    private const val TAG = "OwnerProfilePinRotator"
    private const val PREFS_NAME = "timekpr_owner_lockdown"
    private const val KEY_APPLIED_OTP_SLOT = "applied_otp_slot"

    fun refreshPinIfNeeded(context: Context): Boolean {
        if (Process.myUid() / 100_000 != 0) return false
        if (!DeviceOwnerProvisioner.isDeviceOwner(context)) return false

        val agentToken = TimeKprApplication.from(context).configStore.load().agentToken
        if (agentToken.isNullOrBlank()) return false

        val timeSlot = System.currentTimeMillis() / ParentalAccessOtp.TIME_STEP_MS
        val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        if (prefs.getLong(KEY_APPLIED_OTP_SLOT, -1L) == timeSlot) {
            return true
        }

        val otp = ParentalAccessOtp.generateOtp(agentToken)
        val success = DeviceOwnerProvisioner.resetDevicePassword(context, otp)
        if (!success) {
            Log.w(TAG, "Failed to apply owner profile PIN for OTP slot $timeSlot")
            return false
        }

        prefs.edit().putLong(KEY_APPLIED_OTP_SLOT, timeSlot).apply()
        OwnerProfileLockdown.clearUnlock(context)
        Log.i(TAG, "Updated owner profile PIN for OTP slot $timeSlot")
        return true
    }

    fun markAppliedForCurrentSlot(context: Context) {
        val timeSlot = System.currentTimeMillis() / ParentalAccessOtp.TIME_STEP_MS
        context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putLong(KEY_APPLIED_OTP_SLOT, timeSlot)
            .apply()
    }

    fun clearAppliedSlot(context: Context) {
        context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_APPLIED_OTP_SLOT)
            .apply()
    }
}
