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
import android.widget.TextView
import androidx.core.app.NotificationCompat
import com.timekpr.agent.R
import com.timekpr.agent.boot.OtpLockActivity

/**
 * Persistent top banner shown when screen time is exhausted or owner profile lockdown is active.
 */
object TimeExhaustedOverlay {
    enum class Mode {
        TIME_EXHAUSTED,
        OWNER_LOCKDOWN,
    }

    private const val TAG = "TimeExhaustedOverlay"
    private const val CHANNEL_ID = "timekpr_time_exhausted"
    private const val NOTIFICATION_ID = 1006

    private val handler = Handler(Looper.getMainLooper())
    private var currentView: View? = null
    private var windowManager: WindowManager? = null
    private var fallbackActive = false
    private var currentMode: Mode? = null

    fun show(context: Context, showCallButton: Boolean, mode: Mode = Mode.TIME_EXHAUSTED) {
        handler.post {
            if (!Settings.canDrawOverlays(context)) {
                showNotificationFallback(context.applicationContext, mode)
                return@post
            }
            dismissNotificationFallback(context.applicationContext)
            showOverlay(context.applicationContext, showCallButton, mode)
        }
    }

    fun dismiss(context: Context) {
        handler.post {
            dismissOverlayImmediate()
            dismissNotificationFallback(context.applicationContext)
            currentMode = null
        }
    }

    private fun showOverlay(context: Context, showCallButton: Boolean, mode: Mode) {
        currentMode = mode
        if (currentView != null) {
            updateOverlayContent(currentView!!, showCallButton, context, mode)
            return
        }
        try {
            val themedContext = ContextThemeWrapper(context, R.style.Theme_TimeKprAgent)
            val overlayView = LayoutInflater.from(themedContext)
                .inflate(R.layout.overlay_time_exhausted, null)
            updateOverlayContent(overlayView, showCallButton, context, mode)

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
            showNotificationFallback(context, mode)
        }
    }

    private fun updateOverlayContent(
        overlayView: View,
        showCallButton: Boolean,
        context: Context,
        mode: Mode,
    ) {
        val titleView = overlayView.findViewById<TextView>(R.id.time_exhausted_title)
        val bodyView = overlayView.findViewById<TextView>(R.id.time_exhausted_body)
        when (mode) {
            Mode.TIME_EXHAUSTED -> {
                titleView.setText(R.string.time_exhausted_title)
                bodyView.setText(R.string.time_exhausted_body)
            }
            Mode.OWNER_LOCKDOWN -> {
                titleView.setText(R.string.owner_lockdown_title)
                bodyView.setText(R.string.owner_lockdown_body)
            }
        }
        updateCallButton(overlayView, showCallButton, context)
        setupParentAccessButton(overlayView, context, mode)
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
    private fun setupParentAccessButton(overlayView: View, context: Context, mode: Mode) {
        val parentButton = overlayView.findViewById<Button>(R.id.time_exhausted_parent_access)
        parentButton.setOnClickListener {
            val intent = Intent(context, OtpLockActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            }
            context.startActivity(intent)
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

    private fun showNotificationFallback(context: Context, mode: Mode) {
        if (fallbackActive) {
            return
        }
        val manager = ensureChannel(context)
        val intent = Intent(context, OtpLockActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        }
        val pendingIntent = android.app.PendingIntent.getActivity(
            context,
            0,
            intent,
            android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE
        )

        val titleRes = when (mode) {
            Mode.TIME_EXHAUSTED -> R.string.time_exhausted_title
            Mode.OWNER_LOCKDOWN -> R.string.owner_lockdown_title
        }
        val bodyRes = when (mode) {
            Mode.TIME_EXHAUSTED -> R.string.time_exhausted_body
            Mode.OWNER_LOCKDOWN -> R.string.owner_lockdown_body
        }

        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(context.getString(titleRes))
            .setContentText(context.getString(bodyRes))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .addAction(
                0,
                context.getString(R.string.parental_access_notification_action),
                pendingIntent
            )
            .build()
        manager.notify(NOTIFICATION_ID, notification)
        fallbackActive = true
        currentMode = mode
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
