package com.timekpr.agent.ui

import android.content.Context
import android.graphics.PixelFormat
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
import com.timekpr.agent.R
import com.timekpr.agent.vpn.BlockBurstNotifier

/**
 * Small overlay card shown when a single domain is blocked (requires SYSTEM_ALERT_WINDOW).
 */
object BlockedDomainOverlay {
    private const val TAG = "BlockedDomainOverlay"
    private const val AUTO_DISMISS_MS = 8_000L

    private val handler = Handler(Looper.getMainLooper())
    private var currentView: View? = null
    private var windowManager: WindowManager? = null
    private var dismissRunnable: Runnable? = null

    fun show(context: Context, domain: String): Boolean {
        if (!Settings.canDrawOverlays(context)) {
            Log.d(TAG, "Overlay permission not granted; caller should use notification fallback")
            return false
        }

        handler.post {
            dismissImmediate()
            if (!showInternal(context.applicationContext, domain)) {
                BlockBurstNotifier.showSingleFallback(context.applicationContext, domain)
            }
        }
        return true
    }

    fun dismiss() {
        handler.post { dismissImmediate() }
    }

    private fun showInternal(context: Context, domain: String): Boolean {
        return try {
            val themedContext = ContextThemeWrapper(context, R.style.Theme_TimeKprAgent)
            val inflater = LayoutInflater.from(themedContext)
            val overlayView = inflater.inflate(R.layout.overlay_blocked_domain, null)
            overlayView.findViewById<TextView>(R.id.blocked_domain_message).text =
                context.getString(R.string.domain_block_overlay_body, domain)
            overlayView.findViewById<Button>(R.id.blocked_domain_dismiss).setOnClickListener {
                dismissImmediate()
            }

            val params = WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT,
            ).apply {
                gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
                y = (context.resources.displayMetrics.density * 72).toInt()
            }

            val wm = context.getSystemService(WindowManager::class.java)
            wm.addView(overlayView, params)
            currentView = overlayView
            windowManager = wm
            scheduleAutoDismiss()
            true
        } catch (e: Exception) {
            Log.w(TAG, "Failed to show overlay for $domain", e)
            false
        }
    }

    private fun scheduleAutoDismiss() {
        dismissRunnable?.let { handler.removeCallbacks(it) }
        dismissRunnable = Runnable { dismissImmediate() }.also {
            handler.postDelayed(it, AUTO_DISMISS_MS)
        }
    }

    private fun dismissImmediate() {
        dismissRunnable?.let { handler.removeCallbacks(it) }
        dismissRunnable = null
        val view = currentView ?: return
        val wm = windowManager ?: return
        try {
            wm.removeView(view)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to remove overlay", e)
        }
        currentView = null
        windowManager = null
    }
}
