package com.timekpr.agent.monitor

import android.net.LocalSocket
import android.net.LocalSocketAddress
import com.timekpr.agent.policy.PolicyIpcServer
import org.json.JSONObject
import java.io.PrintWriter

/** Posts alerts from a secondary profile process to the user-0 Policy IPC server. */
object UsageAlertIpcClient {
    fun postAlert(eventType: String, linuxUsername: String, details: JSONObject): Boolean {
        val socket = LocalSocket()
        return try {
            socket.connect(LocalSocketAddress(PolicyIpcServer.SOCKET_NAME))
            val writer = PrintWriter(socket.outputStream, true)
            val payload = JSONObject()
                .put("event_type", eventType)
                .put("linux_username", linuxUsername)
                .put("details", details)
            writer.println("POST_ALERT $payload")
            val response = socket.getInputStream().bufferedReader().readLine()
            response?.contains("\"ok\":true") == true
        } catch (_: Exception) {
            false
        } finally {
            try {
                socket.close()
            } catch (_: Exception) {
            }
        }
    }
}
