package com.guardian.agent.enforcement

import android.telephony.TelephonyManager
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PhoneCallExemptionTest {
    @Test
    fun canMakeCallsRequiresTelephonyAndReadySim() {
        assertTrue(
            PhoneCallExemption.canMakeCalls(
                hasTelephony = true,
                simState = TelephonyManager.SIM_STATE_READY,
            ),
        )
    }

    @Test
    fun canMakeCallsFalseWithoutTelephony() {
        assertFalse(
            PhoneCallExemption.canMakeCalls(
                hasTelephony = false,
                simState = TelephonyManager.SIM_STATE_READY,
            ),
        )
    }

    @Test
    fun canMakeCallsFalseWhenSimNotReady() {
        assertFalse(
            PhoneCallExemption.canMakeCalls(
                hasTelephony = true,
                simState = TelephonyManager.SIM_STATE_ABSENT,
            ),
        )
    }
}
