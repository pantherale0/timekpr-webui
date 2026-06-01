package com.timekpr.agent.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.timekpr.agent.BuildConfig
import com.timekpr.agent.R
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.config.AgentConfig
import com.timekpr.agent.enforcement.EnforcementController
import com.timekpr.agent.monitor.AlertEventBus
import com.timekpr.agent.policy.AppPolicyStore
import com.timekpr.agent.protocol.AgentMessages
import com.timekpr.agent.protocol.CommandDispatcher
import com.timekpr.agent.ui.MainActivity
import com.timekpr.agent.util.AndroidUsers
import com.timekpr.agent.vpn.DomainBlockVpnService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.time.Instant
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

class AgentWebSocketService : Service() {
    private val serviceJob = SupervisorJob()
    private val scope = CoroutineScope(serviceJob + Dispatchers.IO)
    private var socket: WebSocket? = null
    private var reconnectJob: Job? = null

    private lateinit var config: AgentConfig
    private lateinit var appPolicyStore: AppPolicyStore
    private lateinit var commandDispatcher: CommandDispatcher
    private lateinit var enforcement: EnforcementController

    private val client = OkHttpClient.Builder()
        .pingInterval(30, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    override fun onCreate() {
        super.onCreate()
        val app = TimeKprApplication.from(this)
        config = app.configStore.load()
        appPolicyStore = AppPolicyStore(this).also { it.restore() }
        app.domainPolicyStore.restore()

        enforcement = EnforcementController(this, appPolicyStore)
        commandDispatcher = CommandDispatcher(
            context = this,
            appPolicyStore = appPolicyStore,
            onDomainPolicyChanged = {
                DomainBlockVpnService.reconcile(this)
            },
            onAppPolicyChanged = { username ->
                enforcement.applyAppPolicies(username)
            },
            onTimePolicyChanged = { username ->
                enforcement.applyTimePolicies(username)
            },
        )
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFICATION_ID, buildNotification("Connecting to TimeKpr…"))
        reconnectJob?.cancel()
        reconnectJob = scope.launch { runConnectionLoop() }
        return START_STICKY
    }

    override fun onDestroy() {
        reconnectJob?.cancel()
        socket?.close(1000, "service destroyed")
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private suspend fun runConnectionLoop() {
        while (scope.isActive) {
            val latest = TimeKprApplication.from(this).configStore.load()
            if (latest.serverUrl.isBlank()) {
                AgentConnectionState.update(AgentConnectionStatus.ERROR, "Server URL not configured")
                delay(5_000)
                continue
            }
            config = latest
            connectOnce()
            delay(3_000)
        }
    }

    private suspend fun connectOnce() {
        AgentConnectionState.update(AgentConnectionStatus.CONNECTING)
        val request = Request.Builder().url(config.serverUrl).build()
        val connected = kotlinx.coroutines.suspendCancellableCoroutine<Boolean> { continuation ->
            socket = client.newWebSocket(
                request,
                object : WebSocketListener() {
                    override fun onOpen(webSocket: WebSocket, response: Response) {
                        sendHello(webSocket)
                    }

                    override fun onMessage(webSocket: WebSocket, text: String) {
                        handleServerMessage(webSocket, text)
                    }

                    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                        webSocket.close(code, reason)
                    }

                    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                        AgentConnectionState.update(AgentConnectionStatus.DISCONNECTED, reason)
                        if (continuation.isActive) continuation.resumeWith(Result.success(false))
                    }

                    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                        AgentConnectionState.update(
                            AgentConnectionStatus.ERROR,
                            t.message ?: "connection failed",
                        )
                        if (continuation.isActive) continuation.resumeWith(Result.success(false))
                    }
                },
            )
            continuation.invokeOnCancellation {
                socket?.close(1000, "cancelled")
            }
        }
        if (!connected) {
            delay(2_000)
        }
    }

    private fun sendHello(webSocket: WebSocket) {
        val version = config.agentVersion.ifBlank { BuildConfig.DEFAULT_AGENT_VERSION }
        val normalizedVersion = if (version.startsWith("v")) version else "v$version"
        val payload = AgentMessages.hello(
            systemId = config.systemId,
            hostname = AndroidUsers.deviceHostname(this),
            registrationToken = config.registrationToken,
            agentVersion = normalizedVersion,
            linuxUsers = AndroidUsers.linuxUsersPayload(this),
        )
        webSocket.send(payload)
    }

    private fun handleServerMessage(webSocket: WebSocket, raw: String) {
        val message = try {
            JSONObject(raw)
        } catch (_: Exception) {
            return
        }
        when (message.optString("type")) {
            "pairing_status" -> {
                AgentConnectionState.update(AgentConnectionStatus.PENDING_APPROVAL, "Awaiting approval")
                updateNotification("Awaiting admin approval")
            }
            "pairing_approved" -> {
                val token = message.optString("token")
                if (token.isNotBlank()) {
                    TimeKprApplication.from(this).configStore.saveAgentToken(token)
                    TimeKprApplication.from(this).configStore.savePairingComplete(true)
                    config = TimeKprApplication.from(this).configStore.load()
                }
                webSocket.close(1000, "reconnect after pairing")
            }
            "challenge" -> handleChallenge(webSocket, message)
            "auth_result" -> handleAuthResult(webSocket, message)
            "command_request" -> handleCommandRequest(webSocket, message)
            "policy_sync_hint" -> sendPolicySyncCheck(webSocket)
        }
    }

    private fun handleChallenge(webSocket: WebSocket, message: JSONObject) {
        val challenge = message.optString("challenge")
        val token = config.agentToken
        if (challenge.isBlank() || token.isNullOrBlank()) {
            webSocket.close(1008, "missing token")
            return
        }
        val digest = hmacSha256(token, challenge + config.systemId)
        webSocket.send(AgentMessages.register(config.systemId, digest))
    }

    private fun handleAuthResult(webSocket: WebSocket, message: JSONObject) {
        if (!message.optBoolean("success", false)) {
            AgentConnectionState.update(
                AgentConnectionStatus.ERROR,
                message.optString("message", "authentication failed"),
            )
            webSocket.close(1008, "auth failed")
            return
        }
        AgentConnectionState.update(AgentConnectionStatus.AUTHENTICATED, "Connected")
        updateNotification(getString(R.string.agent_notification_body))
        sendAlert(webSocket, "system_startup", JSONObject().put("platform", "android"))
        AlertEventBus.setListener { alert ->
            sendAlert(webSocket, alert.eventType, alert.details)
        }
        sendPolicySyncCheck(webSocket)
        enforcement.startAll()
        scope.launch {
            while (scope.isActive && socket != null) {
                delay(POLICY_SYNC_INTERVAL_MS)
                sendPolicySyncCheck(webSocket)
            }
        }
    }

    private fun handleCommandRequest(webSocket: WebSocket, message: JSONObject) {
        val correlationId = message.optString("correlation_id")
        val action = message.optString("action")
        val username = message.optString("username")
        val args = message.optJSONObject("args") ?: JSONObject()
        val result = commandDispatcher.handle(action, username, args)
        webSocket.send(
            AgentMessages.commandResponse(
                correlationId = correlationId,
                success = result.success,
                message = result.message,
                data = result.data,
            ),
        )
    }

    private fun sendPolicySyncCheck(webSocket: WebSocket) {
        val revisions = TimeKprApplication.from(this).domainPolicyStore.sourceRevisions
        webSocket.send(AgentMessages.policySyncCheck(revisions))
    }

    private fun sendAlert(webSocket: WebSocket, eventType: String, details: JSONObject) {
        val occurredAt = DateTimeFormatter.ISO_INSTANT.format(Instant.now())
        val username = AndroidUsers.currentLinuxUsername(this)
        webSocket.send(
            AgentMessages.alertEvent(
                eventType = eventType,
                occurredAt = occurredAt,
                linuxUsername = username,
                details = details,
            ),
        )
    }

    private fun hmacSha256(secret: String, payload: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(secret.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        return mac.doFinal(payload.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }

    private fun buildNotification(body: String): Notification {
        val launchIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.agent_notification_title))
            .setContentText(body)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentIntent(launchIntent)
            .setOngoing(true)
            .build()
    }

    private fun updateNotification(body: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIFICATION_ID, buildNotification(body))
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.agent_notification_channel),
            NotificationManager.IMPORTANCE_LOW,
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    companion object {
        private const val CHANNEL_ID = "timekpr_agent"
        private const val NOTIFICATION_ID = 1001
        private const val POLICY_SYNC_INTERVAL_MS = 4 * 60 * 60 * 1000L

        fun start(context: Context) {
            context.startForegroundService(Intent(context, AgentWebSocketService::class.java))
        }
    }
}
