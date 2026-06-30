package com.guardian.agent.enforcement

import android.content.Context
import android.content.pm.PackageManager
import com.guardian.agent.policy.DeviceRestrictionPolicy

/**
 * Known Family Link / parental-control bypass vectors mapped to Android packages and
 * user restrictions. Kept in one place for audit against docs/bypass research.
 */
object BypassHardening {
    /** Work-profile creators, DPC test apps, Tasker shortcuts, Samsung Internet sideload vector. */
    val KNOWN_BYPASS_TOOL_PACKAGES = setOf(
        "com.oasisfeng.island",
        "net.typeblog.shelter",
        "com.afwsamples.testdpc",
        "net.dinglisch.android.taskerm",
        "com.sec.android.app.sbrowser",
    )

    /** OEM / AOSP settings entry points used for embedded WebView escapes. */
    val SETTINGS_ESCAPE_PACKAGES = setOf(
        "com.android.settings",
        "com.samsung.android.settings",
    )

    fun extraBlockedPackages(policy: DeviceRestrictionPolicy): Set<String> {
        if (!policy.developerSettingsDisabled && !policy.installAppsDisabled) {
            return emptySet()
        }
        return KNOWN_BYPASS_TOOL_PACKAGES
    }

    fun settingsPackagesForLockout(context: Context): Set<String> {
        val pm = context.packageManager
        return SETTINGS_ESCAPE_PACKAGES.filter { packageName ->
            try {
                pm.getPackageInfo(packageName, 0)
                true
            } catch (_: PackageManager.NameNotFoundException) {
                false
            }
        }.toSet()
    }
}
