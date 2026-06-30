package com.guardian.agent.telemetry

import com.guardian.agent.config.AgentConfig
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL

object AgentRestClient {
    fun restBaseUrl(serverUrl: String): String {
        var restUrl = serverUrl.trim().removeSuffix("/ws")
        if (restUrl.startsWith("ws://")) {
            restUrl = restUrl.replace("ws://", "http://")
        } else if (restUrl.startsWith("wss://")) {
            restUrl = restUrl.replace("wss://", "https://")
        }
        return restUrl.trimEnd('/')
    }

    fun postJson(config: AgentConfig, path: String, body: JSONObject): RestResult {
        val token = config.agentToken?.trim().orEmpty()
        if (config.serverUrl.isBlank() || token.isEmpty()) {
            return RestResult(
                success = false,
                statusCode = 0,
                body = JSONObject().put("success", false).put("message", "Agent not enrolled"),
            )
        }

        val url = URL("${restBaseUrl(config.serverUrl)}$path")
        val conn = url.openConnection() as HttpURLConnection
        return try {
            conn.requestMethod = "POST"
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            conn.setRequestProperty("Authorization", "Bearer $token")
            conn.doOutput = true
            conn.connectTimeout = 15_000
            conn.readTimeout = 15_000

            val bytes = body.toString().toByteArray(Charsets.UTF_8)
            conn.outputStream.use { stream ->
                stream.write(bytes)
            }

            val status = conn.responseCode
            val stream = if (status in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.let { reader ->
                BufferedReader(InputStreamReader(reader, Charsets.UTF_8)).use { it.readText() }
            }.orEmpty()

            val parsed = runCatching { JSONObject(text) }.getOrElse {
                JSONObject().put("success", status in 200..299).put("message", text)
            }
            RestResult(
                success = status in 200..299 && parsed.optBoolean("success", status in 200..299),
                statusCode = status,
                body = parsed,
            )
        } catch (e: Exception) {
            RestResult(
                success = false,
                statusCode = 0,
                body = JSONObject()
                    .put("success", false)
                    .put("message", e.message ?: "request failed"),
            )
        } finally {
            conn.disconnect()
        }
    }

    data class RestResult(
        val success: Boolean,
        val statusCode: Int,
        val body: JSONObject,
    )
}
