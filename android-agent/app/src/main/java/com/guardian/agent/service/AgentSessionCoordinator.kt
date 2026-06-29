package com.guardian.agent.service

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.guardian.agent.GuardianApplication
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.admin.SecondaryUserProvisioner
import com.guardian.agent.enforcement.EnforcementController
import com.guardian.agent.monitor.UsageMonitorService
import com.guardian.agent.protocol.AgentWebSocketClient
import com.guardian.agent.push.PushTokenProvider
import com.guardian.agent.work.AgentSyncWorker
import com.guardian.agent.work.PairingPollWorker
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
        if (SecondaryUserProvisioner.isManagedSecondaryUser(appContext)) {
            SecondaryUserProvisioner.prepareAtLaunch(appContext)
            return
        }

        val configStore = GuardianApplication.from(appContext).configStore
        if (DeviceOwnerProvisioner.isDeviceOwner(appContext) && !configStore.isManagementModeChosen()) {
            return
        }

        DeviceOwnerProvisioner.applyIfDeviceOwner(appContext)
        val app = GuardianApplication.from(appContext)
        app.appPolicyStore.restore()
        app.domainPolicyStore.restore()
        UsageMonitorService.start(appContext)
        schedulePeriodicSync(appContext)
        schedulePairingPollIfNeeded(appContext)
        scheduleSync(appContext, reason = "startup")
    }

    fun scheduleSync(context: Context, reason: String = "manual") {
        if (AgentPersistentConnectionService.isRunning()) {
            return
        }
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
        val config = GuardianApplication.from(context).configStore.load()
        if (config.agentToken.isNullOrBlank()) {
            PairingPollWorker.enqueue(context.applicationContext)
        }
    }

    fun scheduleTokenRegistration(context: Context, token: String) {
        scope.launch {
            GuardianApplication.from(context).configStore.saveFcmToken(token)
            runSyncSession(context, AgentWebSocketClient.SessionMode.SYNC)
        }
    }

    fun handlePairingApproved(context: Context, token: String) {
        GuardianApplication.from(context).configStore.saveAgentToken(token)
        GuardianApplication.from(context).configStore.savePairingComplete(true)
        scheduleSync(context, reason = "pairing_approved")
    }

    suspend fun runSyncSession(
        context: Context,
        mode: AgentWebSocketClient.SessionMode,
    ): AgentWebSocketClient.SessionResult {
        val appContext = context.applicationContext
        val config = GuardianApplication.from(appContext).configStore.load()
        val fcmToken = PushTokenProvider.getToken(appContext)
        if (!fcmToken.isNullOrBlank()) {
            GuardianApplication.from(appContext).configStore.saveFcmToken(fcmToken)
        }
        val client = AgentWebSocketClient.create(appContext)
        return client.runSession(config.copy(agentVersion = config.agentVersion), mode)
    }
}
