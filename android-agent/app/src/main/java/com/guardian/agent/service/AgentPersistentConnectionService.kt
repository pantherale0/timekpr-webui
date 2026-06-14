package com.guardian.agent.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.guardian.agent.R
import com.guardian.agent.GuardianApplication
import com.guardian.agent.protocol.AgentWebSocketClient
import com.guardian.agent.ui.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Maintains a long-lived WebSocket when the server cannot wake the device via FCM
 * (for example, local dev without Firebase credentials configured).
 */
class AgentPersistentConnectionService : Service() {
    private val serviceJob = SupervisorJob()
    private val scope = CoroutineScope(serviceJob + Dispatchers.IO)
    private var connectionJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        running = true
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFICATION_ID, buildNotification(getString(R.string.agent_notification_body_persistent)))
        if (connectionJob?.isActive != true) {
            connectionJob = scope.launch { runConnectionLoop() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        connectionJob?.cancel()
        scope.cancel()
        running = false
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private suspend fun runConnectionLoop() {
        val client = AgentWebSocketClient.create(this)
        while (scope.isActive) {
            val config = GuardianApplication.from(this).configStore.load()
            if (config.serverUrl.isBlank()) {
                AgentConnectionState.update(AgentConnectionStatus.ERROR, "Server URL not configured")
                delay(RECONNECT_DELAY_MS)
                continue
            }
            client.runSession(config, AgentWebSocketClient.SessionMode.PERSISTENT)
            if (scope.isActive) {
                AgentConnectionState.update(AgentConnectionStatus.DISCONNECTED, "Reconnecting…")
                delay(RECONNECT_DELAY_MS)
            }
        }
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

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.agent_notification_channel),
            NotificationManager.IMPORTANCE_LOW,
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    companion object {
        private const val CHANNEL_ID = "guardian_agent_connection"
        private const val NOTIFICATION_ID = 1002
        private const val RECONNECT_DELAY_MS = 3_000L

        @Volatile
        private var running = false

        fun isRunning(): Boolean = running

        fun start(context: Context) {
            val appContext = context.applicationContext
            val intent = Intent(appContext, AgentPersistentConnectionService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                appContext.startForegroundService(intent)
            } else {
                appContext.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.applicationContext.stopService(
                Intent(context.applicationContext, AgentPersistentConnectionService::class.java),
            )
        }
    }
}
