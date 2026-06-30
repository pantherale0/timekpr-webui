package com.guardian.agent.protocol

import android.content.Context
import com.guardian.agent.BuildConfig
import com.guardian.agent.GuardianApplication
import com.guardian.agent.admin.CrossUserStoreSync
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.config.AgentConfig
import com.guardian.agent.discovery.InstalledAppsReporter
import com.guardian.agent.enforcement.EnforcementController
import com.guardian.agent.monitor.AlertEventBus
import com.guardian.agent.policy.AppPolicyStore
import com.guardian.agent.push.PushTokenProvider
import com.guardian.agent.service.AgentConnectionGate
import com.guardian.agent.service.AgentConnectionState
import com.guardian.agent.service.AgentConnectionStatus
import com.guardian.agent.service.AgentPersistentConnectionService
import com.guardian.agent.update.AgentUpdateRequest
import com.guardian.agent.update.AgentUpdateWorker
import com.guardian.agent.util.AndroidUsers
import com.guardian.agent.vpn.DomainBlockVpnService
import com.guardian.agent.work.TelemetryFlushWorker
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.time.Instant
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume

/**
 * Short-lived WebSocket session for FCM / WorkManager wakes (not a 24/7 connection).
 */
class AgentWebSocketClient(
    private val context: Context,
    private val commandDispatcher: CommandDispatcher,
    private val enforcement: EnforcementController,
) {
    private var activeWebSocket: WebSocket? = null

    private val client = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .connectTimeout(20, TimeUnit.SECONDS)
        .build()

    suspend fun runSession(config: AgentConfig, mode: SessionMode): SessionResult {
        if (config.serverUrl.isBlank()) {
            AgentConnectionState.update(AgentConnectionStatus.ERROR, "Server URL not configured")
            return SessionResult(success = false, reason = "missing_server_url")
        }

        return AgentConnectionGate.run(mode) {
            runSessionLocked(config, mode)
        } ?: SessionResult(success = false, reason = "session_busy")
    }

    private suspend fun runSessionLocked(config: AgentConfig, mode: SessionMode): SessionResult {
        val fcmToken = PushTokenProvider.getToken(context)
        if (!fcmToken.isNullOrBlank()) {
            GuardianApplication.from(context).configStore.saveFcmToken(fcmToken)
        }

        AgentConnectionState.update(AgentConnectionStatus.CONNECTING)
        val sessionClient = if (mode == SessionMode.PERSISTENT) {
            client.newBuilder()
                .readTimeout(0, TimeUnit.MILLISECONDS)
                .build()
        } else {
            client
        }
        return suspendCancellableCoroutine { continuation ->
            var completed = false
            val socketRef = arrayOf<WebSocket?>(null)
            fun finish(result: SessionResult) {
                if (completed) return
                completed = true
                socketRef[0]?.close(1000, "session completed")
                if (continuation.isActive) continuation.resume(result)
            }

            val request = Request.Builder().url(config.serverUrl).build()

            socketRef[0] = sessionClient.newWebSocket(
                request,
                object : WebSocketListener() {
                    override fun onOpen(webSocket: WebSocket, response: Response) {
                        webSocket.send(buildHello(config, fcmToken))
                    }

                    override fun onMessage(webSocket: WebSocket, text: String) {
                        handleMessage(webSocket, config, mode, text, ::finish)
                    }

                    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                        clearAlertListener()
                        AgentConnectionState.update(AgentConnectionStatus.DISCONNECTED, reason)
                        if (!completed) finish(SessionResult(success = code == 1000, reason = reason))
                    }

                    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                        clearAlertListener()
                        AgentConnectionState.update(
                            AgentConnectionStatus.ERROR,
                            t.message ?: "connection failed",
                        )
                        finish(SessionResult(success = false, reason = t.message ?: "failure"))
                    }
                },
            )

            continuation.invokeOnCancellation {
                socketRef[0]?.close(1000, "cancelled")
            }
        }
    }

    private fun buildHello(config: AgentConfig, fcmToken: String?): String {
        val version = config.agentVersion.ifBlank { BuildConfig.DEFAULT_AGENT_VERSION }
        val normalizedVersion = if (version.startsWith("v")) version else "v$version"
        return AgentMessages.hello(
            systemId = config.systemId,
            hostname = AndroidUsers.deviceHostname(context),
            registrationToken = config.registrationToken,
            agentVersion = normalizedVersion,
            linuxUsers = AndroidUsers.linuxUsersPayload(context),
            paired = !config.agentToken.isNullOrBlank(),
            fcmToken = fcmToken,
            isDeviceOwner = DeviceOwnerProvisioner.isDeviceOrProfileOwner(context),
        )
    }

    private fun handleMessage(
        webSocket: WebSocket,
        config: AgentConfig,
        mode: SessionMode,
        raw: String,
        onComplete: (SessionResult) -> Unit,
    ) {
        val message = try {
            JSONObject(raw)
        } catch (_: Exception) {
            return
        }

        when (message.optString("type")) {
            "pairing_status" -> {
                AgentConnectionState.update(AgentConnectionStatus.PENDING_APPROVAL, "Awaiting approval")
            }
            "pairing_approved" -> {
                val token = message.optString("token")
                if (token.isNotBlank()) {
                    GuardianApplication.from(context).configStore.saveAgentToken(token)
                    GuardianApplication.from(context).configStore.savePairingComplete(true)
                }
                onComplete(SessionResult(success = true, reason = "pairing_approved"))
            }
            "challenge" -> {
                val challenge = message.optString("challenge")
                val agentToken = GuardianApplication.from(context).configStore.load().agentToken
                if (challenge.isBlank() || agentToken.isNullOrBlank()) {
                    webSocket.close(1008, "missing token")
                    onComplete(SessionResult(success = false, reason = "missing_token"))
                    return
                }
                webSocket.send(
                    AgentMessages.register(
                        config.systemId,
                        uniffi.guardian_agent.generateAuthSignature(agentToken, challenge, config.systemId),
                    ),
                )
            }
            "auth_result" -> {
                if (!message.optBoolean("success", false)) {
                    if (message.optBoolean("update_required", false)) {
                        val updateRequest = AgentUpdateRequest.from(message)
                        if (updateRequest.targetVersion.isNotBlank()) {
                            AgentUpdateWorker.enqueue(context, updateRequest)
                            AgentConnectionState.update(
                                AgentConnectionStatus.ERROR,
                                "Updating to ${updateRequest.targetVersion}…",
                            )
                            onComplete(SessionResult(success = false, reason = "update_scheduled"))
                            return
                        }
                    }
                    AgentConnectionState.update(
                        AgentConnectionStatus.ERROR,
                        message.optString("message", "authentication failed"),
                    )
                    onComplete(SessionResult(success = false, reason = "auth_failed"))
                    return
                }
                AgentConnectionState.update(AgentConnectionStatus.AUTHENTICATED, "Synced")
                activeWebSocket = webSocket
                AlertEventBus.setListener { pending ->
                    activeWebSocket?.let { socket ->
                        sendAlert(socket, pending.eventType, pending.linuxUsername, pending.details)
                    }
                }
                sendAlert(
                    webSocket,
                    "system_startup",
                    AndroidUsers.currentLinuxUsername(context),
                    JSONObject().put("platform", "android"),
                )
                sendPolicySyncCheck(webSocket)
                enforcement.startAll()
                CrossUserStoreSync.replicateToAllSecondaryUsers(context)
                DomainBlockVpnService.reconcile(context)

                AlertEventBus.drain().forEach { pending ->
                    sendAlert(webSocket, pending.eventType, pending.linuxUsername, pending.details)
                }

                TelemetryFlushWorker.enqueue(context)
                GuardianApplication.from(context).telemetryRouter.flushQueuedTelemetry()

                InstalledAppsReporter.reportAllManagedUsers(context, webSocket)
                // reconcileAllUsers in startAll() already applied policies for all local users.

                if (mode == SessionMode.PERSISTENT || message.optBoolean("persistent_connection", false)) {
                    if (mode != SessionMode.PERSISTENT) {
                        AgentPersistentConnectionService.start(context)
                        clearAlertListener()
                        onComplete(SessionResult(success = true, reason = "persistent_handoff"))
                        return
                    }
                    return
                }

                clearAlertListener()
                onComplete(SessionResult(success = true, reason = "sync_complete"))
            }
            "command_request" -> {
                val result = commandDispatcher.handle(
                    message.optString("action"),
                    message.optString("username"),
                    message.optJSONObject("args") ?: JSONObject(),
                )
                webSocket.send(
                    AgentMessages.commandResponse(
                        correlationId = message.optString("correlation_id"),
                        success = result.success,
                        message = result.message,
                        data = result.data,
                    ),
                )
                val followUpApps = result.followUpApps
                val followUpUsername = result.followUpUsername
                if (!followUpApps.isNullOrEmpty() && !followUpUsername.isNullOrBlank()) {
                    InstalledAppsReporter.sendInventory(webSocket, followUpUsername, followUpApps)
                }
            }
            "policy_sync_hint" -> sendPolicySyncCheck(webSocket)
        }
    }

    private fun sendPolicySyncCheck(webSocket: WebSocket) {
        val revisions = GuardianApplication.from(context).domainPolicyStore.sourceRevisions
        webSocket.send(AgentMessages.policySyncCheck(revisions))
    }

    private fun clearAlertListener() {
        activeWebSocket = null
        AlertEventBus.setListener(null)
    }

    private fun sendAlert(
        webSocket: WebSocket,
        eventType: String,
        linuxUsername: String,
        details: JSONObject,
    ) {
        webSocket.send(
            AgentMessages.alertEvent(
                eventType = eventType,
                occurredAt = DateTimeFormatter.ISO_INSTANT.format(Instant.now()),
                linuxUsername = linuxUsername,
                details = details,
            ),
        )
    }


    enum class SessionMode {
        PAIRING_ONLY,
        SYNC,
        PERSISTENT,
    }

    data class SessionResult(val success: Boolean, val reason: String)

    companion object {
        fun create(context: Context): AgentWebSocketClient {
            val app = GuardianApplication.from(context)
            val appPolicyStore = app.appPolicyStore
            app.domainPolicyStore.restore()
            val enforcement = EnforcementController(context, appPolicyStore)
            val dispatcher = CommandDispatcher(
                context = context,
                appPolicyStore = appPolicyStore,
                onDomainPolicyChanged = {
                    CrossUserStoreSync.replicateToAllSecondaryUsers(context)
                    DomainBlockVpnService.reconcile(context)
                },
                onAppPolicyChanged = { username -> enforcement.applyAppPolicies(username) },
                onTimePolicyChanged = { username -> enforcement.applyTimePolicies(username) },
                onDeviceRestrictionChanged = { username -> enforcement.applyDeviceRestrictions(username) },
            )
            return AgentWebSocketClient(context, dispatcher, enforcement)
        }
    }
}
