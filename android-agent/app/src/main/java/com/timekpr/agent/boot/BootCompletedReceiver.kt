package com.timekpr.agent.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class BootCompletedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        when (intent?.action) {
            Intent.ACTION_LOCKED_BOOT_COMPLETED -> {
                DirectBootStartup.onBoot(context, intent.action!!, lockedBoot = true)
            }
            Intent.ACTION_BOOT_COMPLETED -> {
                DirectBootStartup.onBoot(context, intent.action!!, lockedBoot = false)
            }
        }
    }
}
