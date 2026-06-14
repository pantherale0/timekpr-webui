package com.guardian.agent.vpn

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import androidx.core.app.NotificationCompat
import com.guardian.agent.R

internal object BlockBurstNotifier {
    private const val CHANNEL_ID = "guardian_domain_block"
    private const val NOTIFICATION_ID_BURST = 1004
    private const val NOTIFICATION_ID_SINGLE = 1005
    private const val AUTO_DISMISS_MS = 10_000L

    fun showBurst(context: Context) {
        val manager = ensureChannel(context)
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(context.getString(R.string.domain_block_burst_title))
            .setContentText(context.getString(R.string.domain_block_burst_body))
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setOnlyAlertOnce(true)
            .setAutoCancel(true)
            .setTimeoutAfter(AUTO_DISMISS_MS)
            .build()
        manager.notify(NOTIFICATION_ID_BURST, notification)
    }

    fun showSingleFallback(context: Context, domain: String) {
        val manager = ensureChannel(context)
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(context.getString(R.string.domain_block_overlay_title))
            .setContentText(context.getString(R.string.domain_block_overlay_body, domain))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setOnlyAlertOnce(true)
            .setAutoCancel(true)
            .setTimeoutAfter(AUTO_DISMISS_MS)
            .build()
        manager.notify(NOTIFICATION_ID_SINGLE, notification)
    }

    private fun ensureChannel(context: Context): NotificationManager {
        val manager = context.getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val existing = manager.getNotificationChannel(CHANNEL_ID)
            if (existing == null) {
                val channel = NotificationChannel(
                    CHANNEL_ID,
                    context.getString(R.string.domain_block_notification_channel),
                    NotificationManager.IMPORTANCE_DEFAULT,
                ).apply {
                    description = context.getString(R.string.domain_block_notification_channel_desc)
                }
                manager.createNotificationChannel(channel)
            }
        }
        return manager
    }
}
