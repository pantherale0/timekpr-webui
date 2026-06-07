package com.timekpr.agent.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ParentalAccessOtpTest {

    @Test
    fun testGenerateOtpIsDeterministic() {
        val secret = "test_agent_secret_key_12345"
        val timeMs = 1717780000000L // matches 1717780000 in seconds
        
        val code1 = ParentalAccessOtp.generateOtp(secret, timeMs)
        val code2 = ParentalAccessOtp.generateOtp(secret, timeMs)
        
        assertEquals(code1, code2)
        assertEquals(6, code1.length)
        assertEquals("589015", code1) // Verify parity with server-side helper
        assertTrue(code1.all { it.isDigit() })
    }

    @Test
    fun testGenerateOtpChangesAcrossTimeSteps() {
        val secret = "test_agent_secret_key_12345"
        val timeMs1 = 1717780000000L
        val timeMs2 = timeMs1 + (30 * 60 * 1000L) // +30 minutes
        
        val code1 = ParentalAccessOtp.generateOtp(secret, timeMs1)
        val code2 = ParentalAccessOtp.generateOtp(secret, timeMs2)
        
        assertTrue(code1 != code2)
    }

    @Test
    fun testVerifyOtpAcceptsAdjacentWindow() {
        val secret = "test_agent_secret_key_12345"
        val timeMs = 1717780000000L
        val previousOtp = ParentalAccessOtp.generateOtp(secret, timeMs - ParentalAccessOtp.TIME_STEP_MS)

        assertTrue(ParentalAccessOtp.verifyOtp(previousOtp, secret, timeMs))
    }

    @Test
    fun testVerifyOtp() {
        val secret = "test_agent_secret_key_12345"
        val currentOtp = ParentalAccessOtp.generateOtp(secret)
        
        assertTrue(ParentalAccessOtp.verifyOtp(currentOtp, secret))
        assertFalse(ParentalAccessOtp.verifyOtp("123456", secret))
        assertFalse(ParentalAccessOtp.verifyOtp("wrong", secret))
    }
}
