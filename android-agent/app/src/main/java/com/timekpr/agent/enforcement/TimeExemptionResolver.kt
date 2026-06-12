package com.timekpr.agent.enforcement

import android.content.Context
import com.timekpr.agent.policy.TimeLimitStore

class TimeExemptionResolver(
    private val context: Context,
    private val timeLimitStore: TimeLimitStore,
) {
    companion object {
        @Volatile
        var tempExemptSettingsUntil: Long = 0
    }

    fun exemptPackages(username: String): Set<String> {
        val baseExempt = composeExemptPackages(
            agentPackage = context.packageName,
            screentimeExempt = timeLimitStore.screentimeExemptPackages(username),
            phoneExempt = PhoneCallExemption.exemptPackages(context),
            canMakeCalls = PhoneCallExemption.canMakeCalls(context),
        ).toMutableSet()

        if (System.currentTimeMillis() < tempExemptSettingsUntil) {
            baseExempt += "com.android.settings"
        }
        return baseExempt
    }
}
