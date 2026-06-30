package com.guardian.agent.telemetry

import java.io.InputStream
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Length-prefixed frames matching the Linux agent IPC (`u32` native byte order). */
object IpcFraming {
    private const val MAX_FRAME_BYTES = 10 * 1024 * 1024

    fun readFrame(input: InputStream): ByteArray? {
        val header = ByteArray(4)
        if (!readFully(input, header)) {
            return null
        }
        val length = ByteBuffer.wrap(header).order(ByteOrder.nativeOrder()).int
        if (length <= 0 || length > MAX_FRAME_BYTES) {
            throw IllegalArgumentException("Invalid IPC frame length: $length")
        }
        val payload = ByteArray(length)
        if (!readFully(input, payload)) {
            return null
        }
        return payload
    }

    fun writeFrame(output: OutputStream, payload: ByteArray) {
        val header = ByteBuffer.allocate(4)
            .order(ByteOrder.nativeOrder())
            .putInt(payload.size)
            .array()
        output.write(header)
        output.write(payload)
        output.flush()
    }

    private fun readFully(input: InputStream, buffer: ByteArray): Boolean {
        var offset = 0
        while (offset < buffer.size) {
            val read = input.read(buffer, offset, buffer.size - offset)
            if (read < 0) {
                return false
            }
            offset += read
        }
        return true
    }
}
