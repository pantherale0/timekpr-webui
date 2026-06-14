package com.guardian.agent.monitor

import com.guardian.agent.policy.ApprovalPolicy
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ApprovalRequestDeduperTest {
    @Test
    fun dedupesRepeatedTargets() {
        ApprovalRequestDeduper.clearTarget("app_launch", "com.example.game")

        assertTrue(ApprovalRequestDeduper.shouldEmit("app_launch", "com.example.game"))
        assertFalse(ApprovalRequestDeduper.shouldEmit("app_launch", "com.example.game"))
    }

    @Test
    fun clearTargetAllowsAnotherEmit() {
        ApprovalRequestDeduper.clearTarget("domain_access", "blocked.example.com")
        assertTrue(ApprovalRequestDeduper.shouldEmit("domain_access", "blocked.example.com"))
        assertFalse(ApprovalRequestDeduper.shouldEmit("domain_access", "blocked.example.com"))

        ApprovalRequestDeduper.clearTarget("domain_access", "blocked.example.com")
        assertTrue(ApprovalRequestDeduper.shouldEmit("domain_access", "blocked.example.com"))
    }

    @Test
    fun appPolicySyncClearsApprovedPackageDedupes() {
        ApprovalRequestDeduper.clearTarget("app_launch", "com.approved.app")
        assertTrue(ApprovalRequestDeduper.shouldEmit("app_launch", "com.approved.app"))
        assertFalse(ApprovalRequestDeduper.shouldEmit("app_launch", "com.approved.app"))

        ApprovalRequestDeduper.onAppApprovalPolicySynced(
            ApprovalPolicy(
                appLaunchMode = "allowlist",
                approvedPackages = setOf("com.approved.app"),
                blockedPackages = emptySet(),
            ),
        )

        assertTrue(ApprovalRequestDeduper.shouldEmit("app_launch", "com.approved.app"))
    }
}
