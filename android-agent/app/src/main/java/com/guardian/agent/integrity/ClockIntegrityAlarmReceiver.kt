package com.guardian.agent.integrity

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

class ClockIntegrityAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        Log.d(TAG, "Clock integrity alarm fired")
        ClockIntegrityMonitor.ensureServicesRunning(context)
        ClockIntegrityMonitor.tickOnce(context.applicationContext)
        ClockIntegrityMonitor.scheduleAlarmFallback(context.applicationContext)
    }

    companion object {
        private const val TAG = "ClockIntegrityAlarm"
    }
}
