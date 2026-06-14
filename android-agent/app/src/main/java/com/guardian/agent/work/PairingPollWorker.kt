package com.guardian.agent.work

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.guardian.agent.GuardianApplication
import com.guardian.agent.protocol.AgentWebSocketClient
import com.guardian.agent.service.AgentSessionCoordinator
import java.util.concurrent.TimeUnit

/**
 * Low-frequency pairing check while the device is still pending approval.
 * Replaces holding a WebSocket open during enrollment.
 */
class PairingPollWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val config = GuardianApplication.from(applicationContext).configStore.load()
        if (!config.agentToken.isNullOrBlank()) {
            WorkManager.getInstance(applicationContext).cancelUniqueWork(WORK_NAME)
            return Result.success()
        }

        val result = AgentSessionCoordinator.runSyncSession(
            applicationContext,
            AgentWebSocketClient.SessionMode.PAIRING_ONLY,
        )
        if (result.reason == "pairing_approved") {
            AgentSessionCoordinator.scheduleSync(applicationContext, reason = "pairing_approved")
        }
        return Result.success()
    }

    companion object {
        private const val WORK_NAME = "guardian_pairing_poll"

        fun enqueue(context: Context) {
            val request = PeriodicWorkRequestBuilder<PairingPollWorker>(15, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request,
            )
        }
    }
}
