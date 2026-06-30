package com.guardian.agent.telemetry

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

class IpcFramingTest {

    @Test
    fun roundTripFrame() {
        val payload = """{"type":"BROWSER_LOG","logs":[]}""".toByteArray()
        val output = ByteArrayOutputStream()
        IpcFraming.writeFrame(output, payload)

        val written = output.toByteArray()
        val expectedLength = ByteBuffer.allocate(4).order(ByteOrder.nativeOrder()).putInt(payload.size).array()
        assertArrayEquals(expectedLength, written.copyOfRange(0, 4))
        assertArrayEquals(payload, written.copyOfRange(4, written.size))

        val input = ByteArrayInputStream(written)
        val read = IpcFraming.readFrame(input)
        assertArrayEquals(payload, read)
    }

    @Test
    fun readFrameReturnsNullOnEof() {
        val input = ByteArrayInputStream(byteArrayOf())
        assertEquals(null, IpcFraming.readFrame(input))
    }
}
