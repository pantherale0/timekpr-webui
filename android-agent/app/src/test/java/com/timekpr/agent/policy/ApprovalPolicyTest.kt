package com.timekpr.agent.policy

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ApprovalPolicyTest {
    @Test
    fun effectiveBlockedUsesApprovalOverlay() {
        val rulesBlocked = setOf("com.rule.blocked")
        val approval = ApprovalPolicy(
            appLaunchMode = "allowlist",
            approvedPackages = setOf("com.approved.app"),
            blockedPackages = setOf("com.server.blocked", "com.approved.app"),
        )

        val effective = ApprovalPolicy.effectiveBlockedPackages(rulesBlocked, approval)

        assertEquals(setOf("com.server.blocked"), effective)
    }

    @Test
    fun effectiveBlockedFallsBackToRulesWithoutApproval() {
        val rulesBlocked = setOf("com.rule.blocked")
        val effective = ApprovalPolicy.effectiveBlockedPackages(rulesBlocked, null)
        assertEquals(rulesBlocked, effective)
    }

    @Test
    fun allowedDomainMatcherMatchesSubdomains() {
        val matcher = BlockedDomainMatcher.from(setOf("wikipedia.org"))
        assertTrue(matcher.isBlocked("en.wikipedia.org"))
    }
}
