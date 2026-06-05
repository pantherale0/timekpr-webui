package com.timekpr.agent.policy

import org.junit.Assert.assertEquals
import org.junit.Test

class AppPolicyStoreTest {
    @Test
    fun packageNameStripsAndroidPackagePrefix() {
        val rule = AppPolicyRule(
            applicationName = "Chrome",
            executablePath = "/android/package/com.android.chrome",
            matchType = "package",
            preset = "blocked",
        )
        assertEquals("com.android.chrome", rule.packageName)
    }

    @Test
    fun packageNameAcceptsBarePackageId() {
        val rule = AppPolicyRule(
            applicationName = "Chrome",
            executablePath = "com.android.chrome",
            matchType = "package",
            preset = "blocked",
        )
        assertEquals("com.android.chrome", rule.packageName)
    }
}
