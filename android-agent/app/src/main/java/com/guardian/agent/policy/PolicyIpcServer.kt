package com.guardian.agent.policy

import android.content.Context
import android.net.LocalServerSocket
import android.net.LocalSocket
import android.util.Log
import com.guardian.agent.policy.DomainPolicyResolver
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.PrintWriter
import java.util.concurrent.Executors

class PolicyIpcServer(private val context: Context) {
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
        try {
            val reader = BufferedReader(InputStreamReader(socket.inputStream))
            val writer = PrintWriter(socket.outputStream, true)
            val line = reader.readLine() ?: return
            
            if (line.startsWith("GET_POLICY ")) {
                val uid = line.substring("GET_POLICY ".length).trim()
                val androidUserId = uid.toIntOrNull() ?: 0
                val policy = DomainPolicyResolver.loadVpnPolicyForUser(context, androidUserId)
                
                val response = JSONObject()
                    .put("blocked_domains", JSONArray(policy.blockedDomains.toList()))
                    .put("allowed_domains", JSONArray(policy.allowedDomains.toList()))
                
                writer.println(response.toString())
            } else if (line.startsWith("POST_ALERT ")) {
                val payload = JSONObject(line.substring("POST_ALERT ".length))
                val eventType = payload.optString("event_type")
                val linuxUsername = payload.optString("linux_username")
                val details = payload.optJSONObject("details") ?: JSONObject()
                if (eventType.isNotBlank() && linuxUsername.isNotBlank()) {
                    com.guardian.agent.monitor.AlertEventBus.emit(eventType, linuxUsername, details)
                    writer.println(JSONObject().put("ok", true).toString())
                } else {
                    writer.println(JSONObject().put("ok", false).put("error", "invalid payload").toString())
                }
            } else {
                writer.println(JSONObject().put("error", "Unknown command").toString())
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error handling IPC client request", e)
        } finally {
            try {
                socket.close()
            } catch (_: Exception) {}
        }
    }

    companion object {
        private const val TAG = "PolicyIpcServer"
        const val SOCKET_NAME = "com.guardian.agent.policy.socket"
    }
}
