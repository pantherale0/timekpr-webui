package com.timekpr.agent.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Process
import android.util.Log
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.admin.SecondaryUserProvisioner
import com.timekpr.agent.service.AgentSessionCoordinator

/** Starts enforcement when a user profile is unlocked after boot. */
class UserUnlockedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != Intent.ACTION_USER_UNLOCKED) return
        val userId = Process.myUid() / 100_000
        when {
            SecondaryUserProvisioner.isManagedSecondaryUser(context) -> {
                Log.i(TAG, "Managed secondary user unlocked; preparing enforcement")
                SecondaryUserProvisioner.prepareAtLaunch(context)
            }
            userId == 0 -> {
                Log.i(TAG, "Owner user unlocked; starting mobile agent")
                TimeKprApplication.from(context).configStore.migrateToDeviceProtectedStorageIfNeeded()
                AgentSessionCoordinator.startMobileAgent(context)
                SecondaryUserProvisioner.bootstrapAllSecondaryUsers(context)
            }
        }
    }

    companion object {
        private const val TAG = "UserUnlockedReceiver"
    }
}
