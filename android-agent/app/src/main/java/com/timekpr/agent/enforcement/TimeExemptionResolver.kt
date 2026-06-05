package com.timekpr.agent.enforcement

import android.content.Context
import com.timekpr.agent.policy.TimeLimitStore

class TimeExemptionResolver(
    private val context: Context,
    private val timeLimitStore: TimeLimitStore,
) {
    fun exemptPackages(username: String): Set<String> {
        return composeExemptPackages(
            agentPackage = context.packageName,
            screentimeExempt = timeLimitStore.screentimeExemptPackages(username),
            phoneExempt = PhoneCallExemption.exemptPackages(context),
            canMakeCalls = PhoneCallExemption.canMakeCalls(context),
        )
    }
}
