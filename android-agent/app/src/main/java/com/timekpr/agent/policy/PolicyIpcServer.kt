package com.timekpr.agent.policy

import android.content.Context
import android.net.LocalServerSocket
import android.net.LocalSocket
import android.util.Log
import com.timekpr.agent.TimeKprApplication
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
                val domainStore = TimeKprApplication.from(context).domainPolicyStore
                
                val blocked = domainStore.blockedDomainsForUid(uid)
                val allowed = domainStore.policyForUid(uid)?.allowedDomains ?: emptySet()
                
                val response = JSONObject()
                    .put("blocked_domains", JSONArray(blocked.toList()))
                    .put("allowed_domains", JSONArray(allowed.toList()))
                
                writer.println(response.toString())
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
        const val SOCKET_NAME = "com.timekpr.agent.policy.socket"
    }
}
