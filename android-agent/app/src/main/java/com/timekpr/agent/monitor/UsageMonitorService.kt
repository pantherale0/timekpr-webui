package com.timekpr.agent.monitor

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.timekpr.agent.R
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.enforcement.EnforcementController
import com.timekpr.agent.policy.AppPolicyStore
import com.timekpr.agent.util.AndroidUsers
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.time.Instant
import java.time.format.DateTimeFormatter

class UsageMonitorService : Service() {
    private val serviceJob = SupervisorJob()
    private val scope = CoroutineScope(serviceJob + Dispatchers.Default)
    private var monitorJob: Job? = null

    private lateinit var appPolicyStore: AppPolicyStore
    private lateinit var enforcement: EnforcementController
    private val activeSessions = mutableMapOf<String, Long>()

    override fun onCreate() {
        super.onCreate()
        appPolicyStore = AppPolicyStore(this).also { it.restore() }
        enforcement = EnforcementController(this, appPolicyStore)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFICATION_ID, buildNotification())
        monitorJob?.cancel()
        monitorJob = scope.launch { monitorLoop() }
        return START_STICKY
    }

    override fun onDestroy() {
        monitorJob?.cancel()
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private suspend fun monitorLoop() {
        val usageStatsManager = getSystemService(UsageStatsManager::class.java) ?: return
        val username = AndroidUsers.currentLinuxUsername(this)
        val timeStore = TimeKprApplication.from(this).timeLimitStore

        while (scope.isActive) {
            if (!timeStore.isAccessAllowed(username)) {
                enforcement.applyTimePolicies(username)
            }

            val end = System.currentTimeMillis()
            val start = end - 5_000
            val events = usageStatsManager.queryEvents(start, end)
            val event = UsageEvents.Event()
            while (events.hasNextEvent()) {
                events.getNextEvent(event)
                when (event.eventType) {
                    UsageEvents.Event.ACTIVITY_RESUMED -> {
                        val packageName = event.packageName ?: continue
                        if (enforcement.suspendBlockedLaunch(packageName, username)) {
                            emitLocalAlert(
                                "app_blocked",
                                JSONObject()
                                    .put("application_name", packageName)
                                    .put("executable_path", "/android/package/$packageName")
                                    .put("reason", "policy_block"),
                            )
                            continue
                        }
                        activeSessions[packageName] = event.timeStamp
                    }
                    UsageEvents.Event.ACTIVITY_PAUSED,
                    UsageEvents.Event.ACTIVITY_STOPPED -> {
                        val packageName = event.packageName ?: continue
                        val startedAt = activeSessions.remove(packageName) ?: continue
                        val durationSeconds = ((event.timeStamp - startedAt) / 1000).coerceAtLeast(1)
                        timeStore.recordUsage(username, durationSeconds.toInt())
                        emitLocalAlert(
                            "app_usage",
                            JSONObject()
                                .put("application_name", packageName)
                                .put("executable_path", "/android/package/$packageName")
                                .put("duration_seconds", durationSeconds)
                                .put(
                                    "start_time",
                                    DateTimeFormatter.ISO_INSTANT.format(
                                        Instant.ofEpochMilli(startedAt),
                                    ),
                                )
                                .put(
                                    "end_time",
                                    DateTimeFormatter.ISO_INSTANT.format(
                                        Instant.ofEpochMilli(event.timeStamp),
                                    ),
                                ),
                        )
                    }
                }
            }
            delay(2_000)
        }
    }

    private fun emitLocalAlert(eventType: String, details: JSONObject) {
        AlertEventBus.emit(
            eventType = eventType,
            linuxUsername = AndroidUsers.currentLinuxUsername(this),
            details = details,
        )
    }

    private fun buildNotification(): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.agent_notification_title))
            .setContentText("Monitoring app usage")
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setOngoing(true)
            .build()
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "TimeKpr Usage Monitor",
            NotificationManager.IMPORTANCE_LOW,
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    companion object {
        private const val CHANNEL_ID = "timekpr_usage"
        private const val NOTIFICATION_ID = 1003

        fun start(context: Context) {
            context.startForegroundService(Intent(context, UsageMonitorService::class.java))
        }
    }
}
