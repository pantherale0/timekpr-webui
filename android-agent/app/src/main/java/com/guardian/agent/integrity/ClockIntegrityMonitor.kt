package com.guardian.agent.integrity

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import com.guardian.agent.GuardianApplication
import com.guardian.agent.enforcement.EnforcementController
import com.guardian.agent.monitor.AlertEventBus
import com.guardian.agent.monitor.UsageMonitorService
import com.guardian.agent.service.AgentPersistentConnectionService
import com.guardian.agent.util.AndroidUsers
import org.json.JSONObject
import java.util.concurrent.Executors

object ClockIntegrityMonitor {
    private const val TAG = "ClockIntegrityMonitor"
    private const val ALARM_REQUEST_CODE = 42_001
    private const val ALARM_INTERVAL_MS = 60_000L
    private val ntpExecutor = Executors.newSingleThreadExecutor()
    private val alarmWorkExecutor = Executors.newSingleThreadExecutor()

    fun runAlarmWork(context: Context, onComplete: (() -> Unit)? = null) {
        val appContext = context.applicationContext
        alarmWorkExecutor.execute {
            try {
                ensureServicesRunning(appContext)
                tickOnce(appContext)
                scheduleAlarmFallback(appContext)
            } catch (e: Exception) {
                Log.w(TAG, "Clock integrity alarm work failed", e)
            } finally {
                onComplete?.invoke()
            }
        }
    }

    data class TickResult(
        val status: String,
        val tamperActive: Boolean,
        val skewSeconds: Long,
        val detectionSource: String,
    )

    fun tickOnce(context: Context): TickResult {
        val app = GuardianApplication.from(context)
        val store = ClockIntegrityStore(context)
        val wallMs = System.currentTimeMillis()
        val boottimeMs = SystemClock.elapsedRealtime()
        val ntpMs = ntpExecutor.submit<Long?> { NtpClient.queryNtpMs() }.get()

        val rustResult = uniffi.guardian_agent.clockIntegrityTick(
            store.loadStateJson(),
            wallMs,
            boottimeMs,
            ntpMs ?: -1L,
        )
        store.saveStateJson(rustResult.persistedJson)

        val enforcement = EnforcementController(context, app.appPolicyStore)
        when (rustResult.status) {
            "tamper_detected" -> {
                emitTamperAlert(context, rustResult, wallMs, ntpMs)
                enforcement.enforceClockTamperForActiveUser()
            }
            "tamper_cleared" -> enforcement.clearClockTamperForActiveUser()
            else -> {
                if (rustResult.tamperActive) {
                    enforcement.enforceClockTamperForActiveUser()
                }
            }
        }

        return TickResult(
            status = rustResult.status,
            tamperActive = rustResult.tamperActive,
            skewSeconds = rustResult.skewSeconds,
            detectionSource = rustResult.detectionSource,
        )
    }

    fun scheduleAlarmFallback(context: Context) {
        val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
        val intent = Intent(context, ClockIntegrityAlarmReceiver::class.java)
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) PendingIntent.FLAG_IMMUTABLE else 0
        val pending = PendingIntent.getBroadcast(context, ALARM_REQUEST_CODE, intent, flags)
        val triggerAt = SystemClock.elapsedRealtime() + ALARM_INTERVAL_MS
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                alarmManager.setExactAndAllowWhileIdle(
                    AlarmManager.ELAPSED_REALTIME_WAKEUP,
                    triggerAt,
                    pending,
                )
            } else {
                alarmManager.setExact(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, pending)
            }
        } catch (e: SecurityException) {
            Log.w(TAG, "Exact alarm not permitted; relying on foreground loop", e)
        }
    }

    fun cancelAlarmFallback(context: Context) {
        val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
        val intent = Intent(context, ClockIntegrityAlarmReceiver::class.java)
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) PendingIntent.FLAG_IMMUTABLE else 0
        val pending = PendingIntent.getBroadcast(context, ALARM_REQUEST_CODE, intent, flags)
        alarmManager.cancel(pending)
    }

    fun ensureServicesRunning(context: Context) {
        val appContext = context.applicationContext
        if (!UsageMonitorService.isRunning()) {
            UsageMonitorService.start(appContext)
        }
        if (!AgentPersistentConnectionService.isRunning()) {
            AgentPersistentConnectionService.start(appContext)
        }
    }

    private fun emitTamperAlert(
        context: Context,
        rustResult: uniffi.guardian_agent.ClockIntegrityTickResult,
        wallMs: Long,
        ntpMs: Long?,
    ) {
        val activeUid = AndroidUsers.activeUserUid(context)
        val username = AndroidUsers.usernameForUid(
            context,
            activeUid,
            GuardianApplication.from(context).timeLimitStore,
        )
        val autoTime = try {
            Settings.Global.getInt(context.contentResolver, Settings.Global.AUTO_TIME, 1) == 1
        } catch (_: Exception) {
            true
        }
        AlertEventBus.emit(
            "clock_tamper",
            username,
            JSONObject()
                .put("skew_seconds", rustResult.skewSeconds)
                .put("detection_source", rustResult.detectionSource)
                .put("wall_ms", wallMs)
                .put("expected_wall_ms", rustResult.expectedWallMs)
                .put("auto_time_enabled", autoTime)
                .put("ntp_ms", ntpMs ?: JSONObject.NULL),
        )
    }
}
