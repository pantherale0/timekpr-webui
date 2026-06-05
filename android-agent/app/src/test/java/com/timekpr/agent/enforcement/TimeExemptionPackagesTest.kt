package com.timekpr.agent.enforcement

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class TimeExemptionPackagesTest {
    @Test
    fun composeAlwaysIncludesAgentPackage() {
        val exempt = composeExemptPackages(
            agentPackage = "com.timekpr.agent",
            screentimeExempt = emptySet(),
            phoneExempt = setOf("com.google.android.dialer"),
            canMakeCalls = false,
        )
        assertEquals(setOf("com.timekpr.agent"), exempt)
    }

    @Test
    fun composeIncludesScreentimeWhitelistRegardlessOfPhone() {
        val exempt = composeExemptPackages(
            agentPackage = "com.timekpr.agent",
            screentimeExempt = setOf("com.example.education"),
            phoneExempt = setOf("com.google.android.dialer"),
            canMakeCalls = false,
        )
        assertEquals(
            setOf("com.timekpr.agent", "com.example.education"),
            exempt,
        )
    }

    @Test
    fun composeIncludesPhonePackagesWhenCallsAllowed() {
        val exempt = composeExemptPackages(
            agentPackage = "com.timekpr.agent",
            screentimeExempt = setOf("com.example.education"),
            phoneExempt = setOf("com.google.android.dialer", "com.android.incallui"),
            canMakeCalls = true,
        )
        assertEquals(
            setOf(
                "com.timekpr.agent",
                "com.example.education",
                "com.google.android.dialer",
                "com.android.incallui",
            ),
            exempt,
        )
    }

    @Test
    fun composeExcludesPhonePackagesOnTablet() {
        val exempt = composeExemptPackages(
            agentPackage = "com.timekpr.agent",
            screentimeExempt = emptySet(),
            phoneExempt = setOf("com.google.android.dialer"),
            canMakeCalls = false,
        )
        assertFalse(exempt.contains("com.google.android.dialer"))
    }
}
