package com.timekpr.agent.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.enforcement.EnforcementController

class UserSwitchedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != "android.intent.action.USER_SWITCHED") return
        Log.i(TAG, "User switched broadcast received.")
        
        val app = TimeKprApplication.from(context)
        val enforcement = EnforcementController(context, app.appPolicyStore)
        enforcement.reconcileAllUsers()
    }

    companion object {
        private const val TAG = "UserSwitchedReceiver"
    }
}
