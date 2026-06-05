package com.timekpr.agent.vpn

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.monitor.AlertEventBus
import com.timekpr.agent.monitor.ApprovalRequestDeduper
import com.timekpr.agent.monitor.ForegroundAppTracker
import com.timekpr.agent.policy.UidPolicy
import com.timekpr.agent.ui.BlockedDomainOverlay
import com.timekpr.agent.util.AndroidUsers
import org.json.JSONObject

/**
 * Deduplicates domain-block UI: overlay for isolated blocks, notification for bursts.
 */
object BlockNotificationCoordinator {
    private const val TAG = "BlockNotificationCoord"
    private const val WINDOW_MS = 10_000L
    private const val COOLDOWN_MS = 10_000L
    private const val EVAL_DEBOUNCE_MS = 400L
    private const val BURST_EVENT_THRESHOLD = 3
    private const val BURST_DOMAIN_THRESHOLD = 2

    private data class BlockEvent(
        val timestamp: Long,
        val queryDomain: String,
        val registrableDomain: String,
        val foregroundPackage: String?,
    )

    private val handler = Handler(Looper.getMainLooper())
    private val events = mutableListOf<BlockEvent>()
    private val seenQueryDomains = mutableSetOf<String>()
    private var lastShownAt = 0L
    private var evaluateScheduled = false

    fun onDomainBlocked(context: Context, queryDomain: String) {
        handler.post {
            handleBlocked(context.applicationContext, queryDomain)
        }
    }

    fun onVpnStopped() {
        handler.post {
            events.clear()
            seenQueryDomains.clear()
            evaluateScheduled = false
            handler.removeCallbacksAndMessages(null)
            ForegroundAppTracker.stop()
        }
    }

    private fun handleBlocked(context: Context, queryDomain: String) {
        ForegroundAppTracker.ensureStarted(context)

        if (!isScreenOn(context)) {
            Log.d(TAG, "Suppressing block UI for $queryDomain (screen off)")
            return
        }

        val normalized = queryDomain.trim().lowercase().trimEnd('.')
        if (normalized.isEmpty()) return

        emitDomainAccessRequestIfNeeded(context, normalized)

        val now = System.currentTimeMillis()
        pruneOldEvents(now)

        if (normalized in seenQueryDomains) {
            return
        }
        seenQueryDomains.add(normalized)

        events.add(
            BlockEvent(
                timestamp = now,
                queryDomain = normalized,
                registrableDomain = DomainGrouping.registrableDomain(normalized),
                foregroundPackage = ForegroundAppTracker.getForegroundPackage(),
            ),
        )

        if (now - lastShownAt < COOLDOWN_MS) {
            Log.d(TAG, "Accumulating block for $normalized (cooldown active)")
            return
        }

        scheduleEvaluation(context)
    }

    private fun scheduleEvaluation(context: Context) {
        if (evaluateScheduled) {
            handler.removeCallbacks(evaluateRunnable)
        }
        evaluateScheduled = true
        evaluateContext = context.applicationContext
        handler.postDelayed(evaluateRunnable, EVAL_DEBOUNCE_MS)
    }

    private var evaluateContext: Context? = null

    private val evaluateRunnable = Runnable {
        evaluateScheduled = false
        val context = evaluateContext ?: return@Runnable
        evaluateContext = null

        if (!isScreenOn(context)) return@Runnable
        if (System.currentTimeMillis() - lastShownAt < COOLDOWN_MS) return@Runnable

        pruneOldEvents(System.currentTimeMillis())
        if (events.isEmpty()) return@Runnable

        val distinctRegistrable = events.map { it.registrableDomain }.distinct()
        val isBurst = events.size >= BURST_EVENT_THRESHOLD ||
            distinctRegistrable.size >= BURST_DOMAIN_THRESHOLD

        if (isBurst) {
            Log.i(TAG, "Showing burst notification (${events.size} blocks, ${distinctRegistrable.size} domains)")
            BlockedDomainOverlay.dismiss()
            BlockBurstNotifier.showBurst(context)
            markShown()
            return@Runnable
        }

        val displayDomain = distinctRegistrable.firstOrNull() ?: events.first().queryDomain
        val uidPolicy = currentUidPolicy(context)
        val showRequestAccess = uidPolicy?.domainAccessMode == UidPolicy.DOMAIN_ACCESS_APPROVAL_ON_BLOCK
        Log.i(TAG, "Showing overlay for blocked domain $displayDomain")
        if (BlockedDomainOverlay.show(
                context,
                displayDomain,
                showRequestAccess,
                uidPolicy?.linuxUsername ?: AndroidUsers.currentLinuxUsername(context),
            )) {
            markShown()
        } else {
            BlockBurstNotifier.showSingleFallback(context, displayDomain)
            markShown()
        }
    }

    private fun markShown() {
        lastShownAt = System.currentTimeMillis()
        events.clear()
        seenQueryDomains.clear()
    }

    private fun pruneOldEvents(now: Long) {
        val cutoff = now - WINDOW_MS
        events.removeAll { it.timestamp < cutoff }
        if (events.isEmpty()) {
            seenQueryDomains.clear()
        }
    }

    private fun isScreenOn(context: Context): Boolean {
        val powerManager = context.getSystemService(PowerManager::class.java) ?: return true
        return powerManager.isInteractive
    }

    private fun currentUidPolicy(context: Context): UidPolicy? {
        val domainStore = TimeKprApplication.from(context).domainPolicyStore
        val uid = AndroidUsers.currentLinuxUid(context).toString()
        return domainStore.policyForUid(uid)
    }

    private fun emitDomainAccessRequestIfNeeded(context: Context, normalizedDomain: String) {
        val policy = currentUidPolicy(context) ?: return
        if (policy.domainAccessMode != UidPolicy.DOMAIN_ACCESS_APPROVAL_ON_BLOCK) return
        val target = DomainGrouping.registrableDomain(normalizedDomain)
        if (!ApprovalRequestDeduper.shouldEmit("domain_access", target)) return
        AlertEventBus.emit(
            "access_requested",
            policy.linuxUsername,
            JSONObject()
                .put("request_type", "domain_access")
                .put("target_kind", "domain")
                .put("target_value", target)
                .put("display_label", target),
        )
    }
}
