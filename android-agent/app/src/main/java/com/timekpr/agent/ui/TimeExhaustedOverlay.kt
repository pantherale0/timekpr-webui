package com.timekpr.agent.ui

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.ContextThemeWrapper
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.WindowManager
import android.widget.Button
import androidx.core.app.NotificationCompat
import com.timekpr.agent.R

/**
 * Persistent top banner shown when screen time is exhausted or access is otherwise denied.
 */
object TimeExhaustedOverlay {
    private const val TAG = "TimeExhaustedOverlay"
    private const val CHANNEL_ID = "timekpr_time_exhausted"
    private const val NOTIFICATION_ID = 1006

    private val handler = Handler(Looper.getMainLooper())
    private var currentView: View? = null
    private var windowManager: WindowManager? = null
    private var fallbackActive = false

    fun show(context: Context, showCallButton: Boolean) {
        handler.post {
            if (!Settings.canDrawOverlays(context)) {
                showNotificationFallback(context.applicationContext)
                return@post
            }
            dismissNotificationFallback(context.applicationContext)
            showOverlay(context.applicationContext, showCallButton)
        }
    }

    fun dismiss(context: Context) {
        handler.post {
            dismissOverlayImmediate()
            dismissNotificationFallback(context.applicationContext)
        }
    }

    private fun showOverlay(context: Context, showCallButton: Boolean) {
        if (currentView != null) {
            updateCallButton(currentView!!, showCallButton, context)
            return
        }
        try {
            val themedContext = ContextThemeWrapper(context, R.style.Theme_TimeKprAgent)
            val overlayView = LayoutInflater.from(themedContext)
                .inflate(R.layout.overlay_time_exhausted, null)
            updateCallButton(overlayView, showCallButton, context)

            val params = WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT,
            ).apply {
                gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            }

            val wm = context.getSystemService(WindowManager::class.java)
            wm.addView(overlayView, params)
            currentView = overlayView
            windowManager = wm
        } catch (e: Exception) {
            Log.w(TAG, "Failed to show time exhausted overlay", e)
            showNotificationFallback(context)
        }
    }

    private fun updateCallButton(overlayView: View, showCallButton: Boolean, context: Context) {
        val callButton = overlayView.findViewById<Button>(R.id.time_exhausted_call)
        if (showCallButton) {
            callButton.visibility = View.VISIBLE
            callButton.setOnClickListener {
                launchDialer(context)
            }
        } else {
            callButton.visibility = View.GONE
            callButton.setOnClickListener(null)
        }
    }

    private fun launchDialer(context: Context) {
        val intent = Intent(Intent.ACTION_DIAL).apply {
            data = Uri.parse("tel:")
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try {
            context.startActivity(intent)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to launch dialer", e)
        }
    }

    private fun showNotificationFallback(context: Context) {
        if (fallbackActive) {
            return
        }
        val manager = ensureChannel(context)
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(context.getString(R.string.time_exhausted_title))
            .setContentText(context.getString(R.string.time_exhausted_body))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
        manager.notify(NOTIFICATION_ID, notification)
        fallbackActive = true
    }

    private fun dismissNotificationFallback(context: Context) {
        if (!fallbackActive) {
            return
        }
        context.getSystemService(NotificationManager::class.java)
            .cancel(NOTIFICATION_ID)
        fallbackActive = false
    }

    private fun dismissOverlayImmediate() {
        val view = currentView ?: return
        val wm = windowManager ?: return
        try {
            wm.removeView(view)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to remove time exhausted overlay", e)
        }
        currentView = null
        windowManager = null
    }

    private fun ensureChannel(context: Context): NotificationManager {
        val manager = context.getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val existing = manager.getNotificationChannel(CHANNEL_ID)
            if (existing == null) {
                val channel = NotificationChannel(
                    CHANNEL_ID,
                    context.getString(R.string.time_exhausted_notification_channel),
                    NotificationManager.IMPORTANCE_HIGH,
                ).apply {
                    description = context.getString(R.string.time_exhausted_notification_channel_desc)
                }
                manager.createNotificationChannel(channel)
            }
        }
        return manager
    }
}
