package com.guardian.agent.discovery

import android.content.Context
import com.guardian.agent.protocol.AgentMessages
import com.guardian.agent.util.AndroidUsers
import okhttp3.WebSocket
import org.json.JSONObject
import java.time.Instant
import java.time.format.DateTimeFormatter
import java.util.UUID

object InstalledAppsReporter {
    /** Scan launcher apps for each managed Android user and push chunked inventory reports. */
    fun reportAllManagedUsers(context: Context, webSocket: WebSocket) {
        for ((linuxUsername, userContext) in AndroidUsers.inventoryTargets(context)) {
            val apps = InstalledAppsDiscovery.discover(userContext)
            sendInventory(webSocket, linuxUsername, apps)
        }
    }

    fun sendInventory(webSocket: WebSocket, linuxUsername: String, apps: List<DiscoveredApp>) {
        if (apps.isEmpty()) {
            webSocket.send(
                AgentMessages.installedAppsReport(
                    reportId = UUID.randomUUID().toString(),
                    linuxUsername = linuxUsername,
                    chunkIndex = 0,
                    chunkTotal = 1,
                    isFinal = true,
                    reportedAt = DateTimeFormatter.ISO_INSTANT.format(Instant.now()),
                    apps = emptyList(),
                ),
            )
            return
        }

        val sentIconHashes = mutableSetOf<String>()
        val chunks = apps.chunked(InstalledAppsDiscovery.CHUNK_SIZE)
        val reportId = UUID.randomUUID().toString()
        chunks.forEachIndexed { index, chunk ->
            chunk.forEach { app ->
                val iconHash = app.iconHash
                val iconPng = app.iconPng
                if (!iconHash.isNullOrBlank() && iconPng != null && sentIconHashes.add(iconHash)) {
                    webSocket.send(
                        AgentMessages.appIconReport(
                            contentHash = iconHash,
                            mimeType = "image/png",
                            dataBase64 = InstalledAppsDiscovery.iconBase64(iconPng),
                        ),
                    )
                }
            }
            webSocket.send(
                AgentMessages.installedAppsReport(
                    reportId = reportId,
                    linuxUsername = linuxUsername,
                    chunkIndex = index,
                    chunkTotal = chunks.size,
                    isFinal = index == chunks.lastIndex,
                    reportedAt = DateTimeFormatter.ISO_INSTANT.format(Instant.now()),
                    apps = chunk.map { app ->
                        JSONObject()
                            .put("application_name", app.applicationName)
                            .put("identifier", app.identifier)
                            .put("match_type", app.matchType)
                            .put("version_name", app.versionName)
                            .put("icon_hash", app.iconHash)
                    },
                ),
            )
        }
    }
}
