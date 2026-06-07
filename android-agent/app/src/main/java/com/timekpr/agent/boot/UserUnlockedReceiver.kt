package com.timekpr.agent.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.timekpr.agent.admin.SecondaryUserProvisioner

/** Starts enforcement when a managed secondary profile is unlocked. */
class UserUnlockedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != Intent.ACTION_USER_UNLOCKED) return
        if (!SecondaryUserProvisioner.isManagedSecondaryUser(context)) return
        Log.i(TAG, "Managed secondary user unlocked; preparing enforcement")
        SecondaryUserProvisioner.prepareAtLaunch(context)
    }

    companion object {
        private const val TAG = "UserUnlockedReceiver"
    }
}
