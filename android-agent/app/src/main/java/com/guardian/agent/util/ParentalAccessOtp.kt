package com.guardian.agent.util

import java.nio.ByteBuffer
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

object ParentalAccessOtp {
    const val TIME_STEP_MS = 30 * 60 * 1000L

    fun generateOtp(secret: String, timeMs: Long = System.currentTimeMillis()): String {
        if (secret.isBlank()) return "000000"
        val keyBytes = secret.toByteArray(Charsets.UTF_8)
        val timeSlot = timeMs / TIME_STEP_MS
        val buffer = ByteBuffer.allocate(8).putLong(timeSlot)
        val msg = buffer.array()

        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(keyBytes, "HmacSHA256"))
        val hmac = mac.doFinal(msg)

        val offset = hmac.last().toInt() and 0x0f
        val binary = ((hmac[offset].toInt() and 0x7f) shl 24) or
                ((hmac[offset + 1].toInt() and 0xff) shl 16) or
                ((hmac[offset + 2].toInt() and 0xff) shl 8) or
                (hmac[offset + 3].toInt() and 0xff)

        val otp = binary % 1000000
        return String.format("%06d", otp)
    }

    fun verifyOtp(enteredCode: String, secret: String, timeMs: Long = System.currentTimeMillis()): Boolean {
        if (enteredCode.length != 6 || !enteredCode.all { it.isDigit() }) return false
        for (offset in -3..3) {
            val candidate = generateOtp(secret, timeMs + (offset * TIME_STEP_MS))
            if (enteredCode == candidate) return true
        }
        return false
    }
}
