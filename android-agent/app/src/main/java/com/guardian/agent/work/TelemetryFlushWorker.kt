package com.guardian.agent.work

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.guardian.agent.GuardianApplication

class TelemetryFlushWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        if ((android.os.Process.myUid() / 100_000) != 0) {
            return Result.success()
        }
        val flushed = GuardianApplication.from(applicationContext).telemetryRouter.flushQueuedTelemetry()
        return if (flushed >= 0) Result.success() else Result.retry()
    }

    companion object {
        private const val UNIQUE_NAME = "guardian_telemetry_flush"

        fun enqueue(context: Context) {
            if ((android.os.Process.myUid() / 100_000) != 0) {
                return
            }
            val request = OneTimeWorkRequestBuilder<TelemetryFlushWorker>().build()
            WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
                UNIQUE_NAME,
                ExistingWorkPolicy.KEEP,
                request,
            )
        }
    }
}
