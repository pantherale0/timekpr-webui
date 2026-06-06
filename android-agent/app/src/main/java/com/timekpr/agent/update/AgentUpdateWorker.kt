package com.timekpr.agent.update

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.timekpr.agent.service.AgentConnectionState
import com.timekpr.agent.service.AgentConnectionStatus
import com.timekpr.agent.service.AgentSessionCoordinator

class AgentUpdateWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val raw = inputData.getString(KEY_REQUEST_JSON)?.trim().orEmpty()
        val request = AgentUpdateRequest.fromJson(raw)
            ?: return Result.failure()

        AgentConnectionState.update(
            AgentConnectionStatus.ERROR,
            "Updating to ${request.targetVersion}…",
        )

        val updater = AgentUpdater(applicationContext)
        return when (val outcome = updater.performUpdate(request)) {
            is AgentUpdater.UpdateResult.InstallStarted -> Result.success()
            is AgentUpdater.UpdateResult.AlreadyCurrent -> {
                AgentSessionCoordinator.scheduleSync(applicationContext, reason = "agent_updated")
                Result.success()
            }
            is AgentUpdater.UpdateResult.Failure -> {
                AgentConnectionState.update(AgentConnectionStatus.ERROR, outcome.message)
                Result.retry()
            }
        }
    }

    companion object {
        private const val KEY_REQUEST_JSON = "request_json"
        private const val UNIQUE_WORK_NAME = "timekpr_agent_update"

        fun enqueue(context: Context, request: AgentUpdateRequest) {
            val constraints = androidx.work.Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()
            val workRequest = OneTimeWorkRequestBuilder<AgentUpdateWorker>()
                .setConstraints(constraints)
                .setInputData(workDataOf(KEY_REQUEST_JSON to request.toJson().toString()))
                .build()
            WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
                UNIQUE_WORK_NAME,
                ExistingWorkPolicy.KEEP,
                workRequest,
            )
        }
    }
}
