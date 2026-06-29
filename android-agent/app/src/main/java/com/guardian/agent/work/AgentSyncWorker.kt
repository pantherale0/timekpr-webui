package com.guardian.agent.work

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.guardian.agent.protocol.AgentWebSocketClient
import com.guardian.agent.service.AgentSessionCoordinator
import java.util.concurrent.TimeUnit

class AgentSyncWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val reason = inputData.getString(KEY_REASON) ?: "periodic"
        val mode = if (reason == "pairing_poll" || reason == "pairing") {
            AgentWebSocketClient.SessionMode.PAIRING_ONLY
        } else {
            AgentWebSocketClient.SessionMode.SYNC
        }
        val result = AgentSessionCoordinator.runSyncSession(applicationContext, mode)
        if (result.reason == "pairing_approved") {
            AgentSessionCoordinator.scheduleSync(applicationContext, reason = "pairing_approved")
        }
        return when {
            result.success -> Result.success()
            result.reason == "update_scheduled" -> Result.success()
            result.reason == "persistent_handoff" -> Result.success()
            result.reason == "session_busy" -> Result.success()
            else -> Result.retry()
        }
    }

    companion object {
        private const val KEY_REASON = "reason"
        private const val PERIODIC_NAME = "guardian_periodic_sync"

        fun input(reason: String) = androidx.work.Data.Builder()
            .putString(KEY_REASON, reason)
            .build()

        fun uniqueName(reason: String) = "guardian_sync_$reason"

        fun enqueuePeriodic(context: Context) {
            val request = PeriodicWorkRequestBuilder<AgentSyncWorker>(4, TimeUnit.HOURS)
                .setInputData(input("periodic"))
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                PERIODIC_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request,
            )
        }
    }
}
