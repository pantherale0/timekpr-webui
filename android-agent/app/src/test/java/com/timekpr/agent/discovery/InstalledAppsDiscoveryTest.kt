package com.timekpr.agent.discovery

import org.junit.Assert.assertEquals
import org.junit.Test

class InstalledAppsDiscoveryTest {
    @Test
    fun sha256Hex_isStable() {
        val bytes = byteArrayOf(1, 2, 3, 4)
        val hash = InstalledAppsDiscovery.sha256Hex(bytes)
        assertEquals(64, hash.length)
        assertEquals(InstalledAppsDiscovery.sha256Hex(bytes), hash)
    }

    @Test
    fun androidPackagePrefix_isAppliedInIdentifier() {
        assertEquals("/android/package/", InstalledAppsDiscovery.ANDROID_PACKAGE_PREFIX)
        assertEquals("package", InstalledAppsDiscovery.MATCH_TYPE_PACKAGE)
    }
}
