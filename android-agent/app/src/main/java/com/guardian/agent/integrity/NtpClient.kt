package com.guardian.agent.integrity

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

object NtpClient {
    private const val NTP_EPOCH_OFFSET_SECS = 2_208_988_800L

    fun queryNtpMs(host: String = "pool.ntp.org", timeoutMs: Int = 3_000): Long? {
        return try {
            DatagramSocket().use { socket ->
                socket.soTimeout = timeoutMs
                val address = InetAddress.getByName(host)
                val buffer = ByteArray(48)
                buffer[0] = 0x1B
                val request = DatagramPacket(buffer, buffer.size, address, 123)
                socket.send(request)
                val response = DatagramPacket(buffer, buffer.size)
                socket.receive(response)
                val seconds = ((buffer[40].toLong() and 0xFF) shl 24) or
                    ((buffer[41].toLong() and 0xFF) shl 16) or
                    ((buffer[42].toLong() and 0xFF) shl 8) or
                    (buffer[43].toLong() and 0xFF)
                val fraction = ((buffer[44].toLong() and 0xFF) shl 24) or
                    ((buffer[45].toLong() and 0xFF) shl 16) or
                    ((buffer[46].toLong() and 0xFF) shl 8) or
                    (buffer[47].toLong() and 0xFF)
                if (seconds < NTP_EPOCH_OFFSET_SECS) {
                    return null
                }
                val unixSecs = seconds - NTP_EPOCH_OFFSET_SECS
                unixSecs * 1000L + (fraction * 1000L) / 0x1_0000_0000L
            }
        } catch (_: Exception) {
            null
        }
    }
}
