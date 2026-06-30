package com.guardian.agent.notification

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.core.app.NotificationCompat
import com.guardian.agent.R

/** Shows a short user-visible alert when Guardian rules are synced from the server. */
object PolicyUpdateNotifier {
    private const val CHANNEL_ID = "guardian_policy_updates"
    private const val NOTIFICATION_ID = 1007
    private const val DEBOUNCE_MS = 1_500L
    private const val AUTO_DISMISS_MS = 12_000L

    private val handler = Handler(Looper.getMainLooper())
    private var pendingContext: Context? = null

    private val showRunnable = Runnable {
        val context = pendingContext?.applicationContext ?: return@Runnable
        pendingContext = null
        showNow(context)
    }

    fun schedule(context: Context) {
        pendingContext = context.applicationContext
        handler.removeCallbacks(showRunnable)
        handler.postDelayed(showRunnable, DEBOUNCE_MS)
    }

    private fun showNow(context: Context) {
        val manager = ensureChannel(context)
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(context.getString(R.string.policy_update_title))
            .setContentText(context.getString(R.string.policy_update_body))
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setOnlyAlertOnce(true)
            .setAutoCancel(true)
            .setTimeoutAfter(AUTO_DISMISS_MS)
            .build()
        manager.notify(NOTIFICATION_ID, notification)
    }

    private fun ensureChannel(context: Context): NotificationManager {
        val manager = context.getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val existing = manager.getNotificationChannel(CHANNEL_ID)
            if (existing == null) {
                val channel = NotificationChannel(
                    CHANNEL_ID,
                    context.getString(R.string.policy_update_notification_channel),
                    NotificationManager.IMPORTANCE_DEFAULT,
                ).apply {
                    description = context.getString(R.string.policy_update_notification_channel_desc)
                }
                manager.createNotificationChannel(channel)
            }
        }
        return manager
    }
}
