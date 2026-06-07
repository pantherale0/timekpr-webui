package com.timekpr.agent.monitor

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.IBinder
import android.os.Process
import androidx.core.app.NotificationCompat
import com.timekpr.agent.R
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.discovery.PackageChangeMonitor
import com.timekpr.agent.admin.CrossUserStoreSync
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.admin.SecondaryUserProvisioner
import com.timekpr.agent.boot.SecondaryUserInitService
import com.timekpr.agent.enforcement.EnforcementController
import com.timekpr.agent.policy.PolicyPayloadPush
import com.timekpr.agent.policy.PolicyStorePayloadPush
import com.timekpr.agent.vpn.DomainBlockVpnService
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
    private lateinit var packageChangeMonitor: PackageChangeMonitor
    private val activeSessions = mutableMapOf<String, Long>()
    private val processedResumeKeys = LinkedHashSet<String>()
    private var capabilityCheckCounter = 0
    private var lastBootstrappedForegroundUser = -1

    override fun onCreate() {
        super.onCreate()
        appPolicyStore = TimeKprApplication.from(this).appPolicyStore
        enforcement = EnforcementController(this, appPolicyStore)
        packageChangeMonitor = PackageChangeMonitor(this, enforcement)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFICATION_ID, buildNotification())
        packageChangeMonitor.register()
        monitorJob?.cancel()
        monitorJob = scope.launch { monitorLoop() }
        return START_STICKY
    }

    override fun onDestroy() {
        packageChangeMonitor.unregister()
        monitorJob?.cancel()
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private suspend fun monitorLoop() {
        val timeStore = TimeKprApplication.from(this).timeLimitStore

        while (scope.isActive) {
            val activeUid = AndroidUsers.activeUserUid(this)
            if (Process.myUid() / 100_000 == 0 && activeUid != 0 && activeUid != lastBootstrappedForegroundUser) {
                lastBootstrappedForegroundUser = activeUid
                CrossUserStoreSync.replicateFromPrimaryToUser(this, activeUid)
                PolicyStorePayloadPush.pushToUser(this, activeUid)
                PolicyPayloadPush.pushToUser(this, activeUid, activeUid)
                SecondaryUserInitService.startOnUser(this, activeUid)
                userHandleForId(activeUid)?.let { handle ->
                    sendBroadcastAsUser(Intent(DomainBlockVpnService.ACTION_RELOAD_POLICY), handle)
                }
            }
            val username = timeStore.getUsernameForUid(activeUid) ?: AndroidUsers.currentLinuxUsername(this)
            
            val userContext = AndroidUsers.getUserContext(this, activeUid) ?: this
            if (++capabilityCheckCounter % 15 == 0) {
                enforceManagedCapabilities(userContext)
            }
            val usageStatsManager = userContext.getSystemService(UsageStatsManager::class.java)

            if (usageStatsManager != null) {
                val accessAllowed = timeStore.isAccessAllowed(username)
                if (!accessAllowed) {
                    enforcement.applyTimePoliciesForUser(username, activeUid)
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
                            val resumeKey = "$activeUid:$packageName:${event.timeStamp}"
                            if (resumeKey in processedResumeKeys) {
                                continue
                            }
                            processedResumeKeys.add(resumeKey)
                            while (processedResumeKeys.size > 200) {
                                val oldest = processedResumeKeys.first()
                                processedResumeKeys.remove(oldest)
                            }
                            if (enforcement.suspendBlockedLaunch(packageName, username)) {
                                emitBlockedLaunchAlerts(packageName, username, accessAllowed)
                                continue
                            }
                            if (accessAllowed) {
                                activeSessions["$activeUid:$packageName"] = event.timeStamp
                            }
                        }
                        UsageEvents.Event.ACTIVITY_PAUSED,
                        UsageEvents.Event.ACTIVITY_STOPPED -> {
                            if (!accessAllowed) {
                                continue
                            }
                            val packageName = event.packageName ?: continue
                            val startedAt = activeSessions.remove("$activeUid:$packageName") ?: continue
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
                                username
                            )
                        }
                    }
                }
            }
            delay(2_000)
        }
    }

    private fun emitBlockedLaunchAlerts(
        packageName: String,
        username: String,
        accessAllowed: Boolean,
    ) {
        val displayLabel = applicationLabel(packageName) ?: packageName
        if (!accessAllowed) {
            emitLocalAlert(
                "app_blocked",
                JSONObject()
                    .put("application_name", displayLabel)
                    .put("executable_path", "/android/package/$packageName")
                    .put("reason", "screen_time_exhausted"),
                username
            )
            return
        }
        val approval = appPolicyStore.approvalPolicyForUser(username)
        if (approval != null && ApprovalRequestDeduper.shouldEmit("app_launch", packageName)) {
            emitLocalAlert(
                "access_requested",
                JSONObject()
                    .put("request_type", "app_launch")
                    .put("target_kind", "package")
                    .put("target_value", packageName)
                    .put("display_label", displayLabel),
                username
            )
            emitLocalAlert(
                "app_blocked",
                JSONObject()
                    .put("application_name", displayLabel)
                    .put("executable_path", "/android/package/$packageName")
                    .put("reason", "not_approved"),
                username
            )
            return
        }
        emitLocalAlert(
            "app_blocked",
            JSONObject()
                .put("application_name", displayLabel)
                .put("executable_path", "/android/package/$packageName")
                .put("reason", "policy_block"),
            username
        )
    }

    private fun applicationLabel(packageName: String): String? {
        return try {
            val pm = packageManager
            val appInfo = pm.getApplicationInfo(packageName, 0)
            pm.getApplicationLabel(appInfo)?.toString()
        } catch (_: PackageManager.NameNotFoundException) {
            null
        }
    }

    private fun enforceManagedCapabilities(userContext: Context) {
        if (!SecondaryUserProvisioner.isManagedSecondaryUser(userContext) &&
            !DeviceOwnerProvisioner.isDeviceOrProfileOwner(userContext)
        ) {
            return
        }
        if (SecondaryUserProvisioner.isManagedSecondaryUser(userContext)) {
            CrossUserStoreSync.replicateFromPrimaryToCurrentUser(userContext)
            TimeKprApplication.from(userContext).domainPolicyStore.restore()
        }
        if (!DeviceOwnerProvisioner.hasUsageAccess(userContext)) {
            DeviceOwnerProvisioner.applyManagedCapabilities(userContext)
        }
        DomainBlockVpnService.reconcile(userContext)
    }

    private fun userHandleForId(userId: Int): android.os.UserHandle? {
        return try {
            val constructor = android.os.UserHandle::class.java.getConstructor(Int::class.javaPrimitiveType)
            constructor.newInstance(userId) as android.os.UserHandle
        } catch (_: Exception) {
            null
        }
    }

    private fun emitLocalAlert(eventType: String, details: JSONObject, username: String) {
        AlertEventBus.emit(
            eventType = eventType,
            linuxUsername = username,
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
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "TimeKpr Usage Monitor",
                NotificationManager.IMPORTANCE_LOW,
            )
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    companion object {
        private const val CHANNEL_ID = "timekpr_usage"
        private const val NOTIFICATION_ID = 1003

        fun start(context: Context) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(Intent(context, UsageMonitorService::class.java))
            } else {
                context.startService(Intent(context, UsageMonitorService::class.java))
            }
        }
    }
}
