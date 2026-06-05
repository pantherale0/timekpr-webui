package com.timekpr.agent.service

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.enforcement.EnforcementController
import com.timekpr.agent.monitor.UsageMonitorService
import com.timekpr.agent.policy.AppPolicyStore
import com.timekpr.agent.protocol.AgentWebSocketClient
import com.timekpr.agent.push.PushTokenProvider
import com.timekpr.agent.work.AgentSyncWorker
import com.timekpr.agent.work.PairingPollWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * Schedules battery-friendly agent sync instead of a 24/7 WebSocket foreground service.
 */
object AgentSessionCoordinator {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun startMobileAgent(context: Context) {
        val appContext = context.applicationContext
        AppPolicyStore(appContext).restore()
        TimeKprApplication.from(appContext).domainPolicyStore.restore()
        UsageMonitorService.start(appContext)
        schedulePeriodicSync(appContext)
        schedulePairingPollIfNeeded(appContext)
        scheduleSync(appContext, reason = "startup")
    }

    fun scheduleSync(context: Context, reason: String = "manual") {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()
        val request = OneTimeWorkRequestBuilder<AgentSyncWorker>()
            .setConstraints(constraints)
            .setInputData(AgentSyncWorker.input(reason))
            .build()
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
            AgentSyncWorker.uniqueName(reason),
            ExistingWorkPolicy.REPLACE,
            request,
        )
    }

    fun schedulePeriodicSync(context: Context) {
        AgentSyncWorker.enqueuePeriodic(context.applicationContext)
    }

    fun schedulePairingPollIfNeeded(context: Context) {
        val config = TimeKprApplication.from(context).configStore.load()
        if (config.agentToken.isNullOrBlank()) {
            PairingPollWorker.enqueue(context.applicationContext)
        }
    }

    fun scheduleTokenRegistration(context: Context, token: String) {
        scope.launch {
            TimeKprApplication.from(context).configStore.saveFcmToken(token)
            runSyncSession(context, AgentWebSocketClient.SessionMode.SYNC)
        }
    }

    fun handlePairingApproved(context: Context, token: String) {
        TimeKprApplication.from(context).configStore.saveAgentToken(token)
        TimeKprApplication.from(context).configStore.savePairingComplete(true)
        scheduleSync(context, reason = "pairing_approved")
    }

    suspend fun runSyncSession(
        context: Context,
        mode: AgentWebSocketClient.SessionMode,
    ): AgentWebSocketClient.SessionResult {
        val appContext = context.applicationContext
        val config = TimeKprApplication.from(appContext).configStore.load()
        val fcmToken = PushTokenProvider.getToken(appContext)
        if (!fcmToken.isNullOrBlank()) {
            TimeKprApplication.from(appContext).configStore.saveFcmToken(fcmToken)
        }
        val client = AgentWebSocketClient.create(appContext)
        return client.runSession(config.copy(agentVersion = config.agentVersion), mode)
    }
}
