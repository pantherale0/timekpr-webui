package com.timekpr.agent.policy

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BlockedDomainMatcherTest {
    @Test
    fun blocksExactDomain() {
        val matcher = BlockedDomainMatcher.from(setOf("facebook.com"))
        assertTrue(matcher.isBlocked("facebook.com"))
        assertTrue(matcher.isBlocked("facebook.com."))
        assertTrue(matcher.isBlocked("FACEBOOK.COM"))
    }

    @Test
    fun blocksSubdomains() {
        val matcher = BlockedDomainMatcher.from(setOf("facebook.com"))
        assertTrue(matcher.isBlocked("m.facebook.com"))
        assertTrue(matcher.isBlocked("www.ads.facebook.com"))
    }

    @Test
    fun doesNotBlockUnrelatedDomains() {
        val matcher = BlockedDomainMatcher.from(setOf("facebook.com"))
        assertFalse(matcher.isBlocked("notfacebook.com"))
        assertFalse(matcher.isBlocked("facebook.com.evil.net"))
        assertFalse(matcher.isBlocked("google.com"))
    }

    @Test
    fun handlesLargeList() {
        val domains = (0 until 50_000).map { "blocked-$it.example.com" }.toSet()
        val matcher = BlockedDomainMatcher.from(domains)
        assertTrue(matcher.isBlocked("blocked-12345.example.com"))
        assertTrue(matcher.isBlocked("cdn.blocked-999.example.com"))
        assertFalse(matcher.isBlocked("allowed.example.com"))
    }
}
