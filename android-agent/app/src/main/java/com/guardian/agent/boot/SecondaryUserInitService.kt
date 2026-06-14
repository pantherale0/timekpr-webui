package com.guardian.agent.boot

import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.util.Log
import com.guardian.agent.admin.SecondaryUserProvisioner

/**
 * Lightweight entry point started on secondary users by the device owner (user 0)
 * because implicit broadcasts may not wake the full agent process reliably.
 */
class SecondaryUserInitService : Service() {
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (SecondaryUserProvisioner.isManagedSecondaryUser(this)) {
            Log.i(TAG, "Initializing managed secondary user ${android.os.Process.myUid() / 100_000}")
            SecondaryUserProvisioner.prepareAtLaunch(this)
        }
        stopSelf(startId)
        return START_NOT_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val TAG = "SecondaryUserInit"

        fun startOnUser(primaryContext: Context, userId: Int) {
            if (userId == 0) return
            try {
                val constructor = android.os.UserHandle::class.java.getConstructor(Int::class.javaPrimitiveType)
                val userHandle = constructor.newInstance(userId) as android.os.UserHandle
                val intent = Intent(primaryContext, SecondaryUserInitService::class.java)
                val method = Context::class.java.getMethod(
                    "startServiceAsUser",
                    Intent::class.java,
                    android.os.UserHandle::class.java,
                )
                method.invoke(primaryContext, intent, userHandle)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to start init service on user $userId", e)
            }
        }
    }
}
