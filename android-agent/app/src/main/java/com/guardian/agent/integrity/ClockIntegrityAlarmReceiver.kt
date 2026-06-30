package com.guardian.agent.integrity

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

class ClockIntegrityAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        Log.d(TAG, "Clock integrity alarm fired")
        val pendingResult = goAsync()
        ClockIntegrityMonitor.runAlarmWork(context.applicationContext) {
            pendingResult.finish()
        }
    }

    companion object {
        private const val TAG = "ClockIntegrityAlarm"
    }
}
