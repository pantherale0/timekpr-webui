package com.guardian.agent.enforcement

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.telecom.TelecomManager
import android.telephony.TelephonyManager

/**
 * Determines whether regular phone calls should remain available during screen-time lockout
 * and resolves OEM-agnostic dialer / in-call package names.
 */
object PhoneCallExemption {
    fun canMakeCalls(context: Context): Boolean {
        return canMakeCalls(
            hasTelephony = context.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY),
            simState = telephonySimState(context),
        )
    }

    internal fun canMakeCalls(hasTelephony: Boolean, simState: Int): Boolean {
        return hasTelephony && simState == TelephonyManager.SIM_STATE_READY
    }

    fun exemptPackages(context: Context): Set<String> {
        val packages = mutableSetOf<String>()
        packages += context.packageName
        resolveDialerPackage(context)?.let { packages += it }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val telecom = context.getSystemService(TelecomManager::class.java)
            telecom?.defaultDialerPackage?.takeIf { it.isNotBlank() }?.let { packages += it }
        }
        resolveInCallPackage(context)?.let { packages += it }
        return packages
    }

    private fun telephonySimState(context: Context): Int {
        return try {
            val tm = context.getSystemService(TelephonyManager::class.java)
                ?: return TelephonyManager.SIM_STATE_UNKNOWN
            tm.simState
        } catch (_: SecurityException) {
            TelephonyManager.SIM_STATE_UNKNOWN
        }
    }

    private fun resolveDialerPackage(context: Context): String? {
        val intent = Intent(Intent.ACTION_DIAL).apply {
            data = Uri.parse("tel:")
        }
        val resolveInfo = context.packageManager.resolveActivity(
            intent,
            PackageManager.MATCH_DEFAULT_ONLY,
        )
        return resolveInfo?.activityInfo?.packageName
    }

    private fun resolveInCallPackage(context: Context): String? {
        val intent = Intent(Intent.ACTION_CALL).apply {
            data = Uri.parse("tel:")
        }
        val resolveInfo = context.packageManager.resolveActivity(
            intent,
            PackageManager.MATCH_DEFAULT_ONLY,
        )
        return resolveInfo?.activityInfo?.packageName
    }
}
