package com.guardian.agent.policy

import android.content.Context
import android.net.LocalServerSocket
import android.net.LocalSocket
import android.util.Log
import com.guardian.agent.telemetry.AgentTelemetryRouter
import com.guardian.agent.telemetry.IpcFraming
import com.guardian.agent.monitor.AlertEventBus
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.PrintWriter
import java.nio.charset.StandardCharsets
import java.util.concurrent.Executors

class PolicyIpcServer(
    private val context: Context,
    private val telemetryRouter: AgentTelemetryRouter,
) {
    private var serverSocket: LocalServerSocket? = null
    private val executor = Executors.newCachedThreadPool()
    @Volatile private var running = false

    fun start() {
        if (running) return
        running = true
        executor.execute {
            try {
                serverSocket = LocalServerSocket(SOCKET_NAME)
                Log.i(TAG, "Policy IPC server started on abstract address: $SOCKET_NAME")
                while (running) {
                    val socket = serverSocket?.accept() ?: break
                    executor.execute { handleClient(socket) }
                }
            } catch (e: Exception) {
                Log.e(TAG, "Error in Policy IPC server loop", e)
            }
        }
    }

    fun stop() {
        running = false
        try {
            serverSocket?.close()
        } catch (_: Exception) {}
        serverSocket = null
        executor.shutdownNow()
    }

    private fun handleClient(socket: LocalSocket) {
        socket.inputStream.use { input ->
            socket.outputStream.use { output ->
                val header = ByteArray(4)
                if (!readFully(input, header)) {
                    return
                }
                if (isLineProtocolPrefix(header)) {
                    handleLineClient(header, input, output)
                } else {
                    handleFramedClient(header, input, output)
                }
            }
        }
        try {
            socket.close()
        } catch (_: Exception) {}
    }

    private fun handleLineClient(initialBytes: ByteArray, input: java.io.InputStream, output: java.io.OutputStream) {
        val prefix = String(initialBytes, StandardCharsets.US_ASCII)
        val reader = BufferedReader(InputStreamReader(input, StandardCharsets.UTF_8))
        val remainder = reader.readLine() ?: ""
        val line = prefix + remainder
        val writer = PrintWriter(output, true)

        when {
            line.startsWith("GET_POLICY ") -> {
                val uid = line.substring("GET_POLICY ".length).trim()
                val androidUserId = uid.toIntOrNull() ?: 0
                val policy = DomainPolicyResolver.loadVpnPolicyForUser(context, androidUserId)
                val response = JSONObject()
                    .put("blocked_domains", JSONArray(policy.blockedDomains.toList()))
                    .put("allowed_domains", JSONArray(policy.allowedDomains.toList()))
                writer.println(response.toString())
            }
            line.startsWith("POST_ALERT ") -> {
                handlePostAlertLine(line.substring("POST_ALERT ".length), writer)
            }
            else -> {
                writer.println(JSONObject().put("error", "Unknown command").toString())
            }
        }
    }

    private fun handleFramedClient(initialBytes: ByteArray, input: java.io.InputStream, output: java.io.OutputStream) {
        var header: ByteArray? = initialBytes
        while (header != null) {
            val length = java.nio.ByteBuffer.wrap(header)
                .order(java.nio.ByteOrder.nativeOrder())
                .int
            if (length <= 0 || length > 10 * 1024 * 1024) {
                break
            }
            val payload = ByteArray(length)
            if (!readFully(input, payload)) {
                break
            }

            val response = telemetryRouter.handleFramedRequest(payload)
            IpcFraming.writeFrame(output, response.toString().toByteArray(StandardCharsets.UTF_8))

            header = ByteArray(4)
            if (!readFully(input, header)) {
                break
            }
        }
    }

    private fun handlePostAlertLine(payloadJson: String, writer: PrintWriter) {
        try {
            val payload = JSONObject(payloadJson)
            val eventType = payload.optString("event_type")
            val linuxUsername = payload.optString("linux_username")
            val details = payload.optJSONObject("details") ?: JSONObject()
            if (eventType.isBlank() || linuxUsername.isBlank()) {
                writer.println(JSONObject().put("ok", false).put("error", "invalid payload").toString())
                return
            }
            when (eventType.lowercase()) {
                "dialogue_flag", "sentiment_breach" -> {
                    val framed = JSONObject()
                        .put("type", eventType.uppercase())
                        .put("platform", details.optString("platform", "unknown"))
                        .put("details", details)
                    telemetryRouter.handleFramedRequest(
                        framed.toString().toByteArray(StandardCharsets.UTF_8),
                        linuxUsername,
                    )
                }
                else -> AlertEventBus.emit(eventType, linuxUsername, details)
            }
            writer.println(JSONObject().put("ok", true).toString())
        } catch (e: Exception) {
            Log.e(TAG, "Error handling POST_ALERT", e)
            writer.println(JSONObject().put("ok", false).put("error", "invalid payload").toString())
        }
    }

    private fun isLineProtocolPrefix(header: ByteArray): Boolean {
        val prefix = String(header, StandardCharsets.US_ASCII)
        return prefix == "GET_" || prefix == "POST"
    }

    private fun readFully(input: java.io.InputStream, buffer: ByteArray): Boolean {
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

    companion object {
        private const val TAG = "PolicyIpcServer"
        const val SOCKET_NAME = "com.guardian.agent.policy.socket"
    }
}
