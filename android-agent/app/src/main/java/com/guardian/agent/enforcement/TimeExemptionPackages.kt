package com.guardian.agent.enforcement

internal fun composeExemptPackages(
    agentPackage: String,
    screentimeExempt: Set<String>,
    phoneExempt: Set<String>,
    canMakeCalls: Boolean,
): Set<String> {
    val exempt = mutableSetOf(agentPackage)
    exempt += screentimeExempt
    if (canMakeCalls) {
        exempt += phoneExempt
    }
    return exempt
}
