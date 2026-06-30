package com.guardian.agent.enforcement

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.guardian.agent.GuardianApplication
import com.guardian.agent.admin.SecondaryUserProvisioner
import com.guardian.agent.notification.PolicyUpdateNotifier
import java.util.concurrent.Executors

/**
 * Runs heavy enforcement work off the main thread to avoid background ANRs.
 *
 * Policy reconciliation touches DevicePolicyManager, VPN setup, and multiple stores;
 * broadcast receivers and [android.app.Service.onStartCommand] must not block the main
 * looper while doing that work.
 */
object EnforcementCoordinator {
    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "guardian-enforcement").apply { isDaemon = true }
    }
    private val debounceHandler = Handler(Looper.getMainLooper())
    private var pendingPolicyReloadContext: Context? = null

    private val debouncedPolicyReloadRunnable = Runnable {
        val context = pendingPolicyReloadContext ?: return@Runnable
        pendingPolicyReloadContext = null
        reloadPolicyStoresAndReconcile(context)
    }

    fun scheduleReconcile(context: Context, onComplete: (() -> Unit)? = null) {
        val appContext = context.applicationContext
        executor.execute {
            try {
                val app = GuardianApplication.from(appContext)
                EnforcementController(appContext, app.appPolicyStore).reconcileAllUsers()
            } finally {
                onComplete?.invoke()
            }
        }
    }

    fun scheduleApplyAppPolicies(context: Context, username: String, onComplete: (() -> Unit)? = null) {
        val appContext = context.applicationContext
        executor.execute {
            try {
                val app = GuardianApplication.from(appContext)
                EnforcementController(appContext, app.appPolicyStore).applyAppPolicies(username)
            } finally {
                onComplete?.invoke()
            }
        }
    }

    fun schedulePrepareManagedSecondaryUser(context: Context, onComplete: (() -> Unit)? = null) {
        val appContext = context.applicationContext
        executor.execute {
            try {
                SecondaryUserProvisioner.prepareAtLaunch(appContext)
            } finally {
                onComplete?.invoke()
            }
        }
    }

    /** Debounced policy-store reload used when primary user pushes prefs to secondary profiles. */
    fun schedulePolicyReloadAndReconcileDebounced(context: Context, debounceMs: Long = 400L) {
        pendingPolicyReloadContext = context.applicationContext
        debounceHandler.removeCallbacks(debouncedPolicyReloadRunnable)
        debounceHandler.postDelayed(debouncedPolicyReloadRunnable, debounceMs)
    }

    private fun reloadPolicyStoresAndReconcile(context: Context) {
        executor.execute {
            val app = GuardianApplication.from(context)
            app.timeLimitStore.reloadFromPrefs()
            app.appPolicyStore.restore()
            app.domainPolicyStore.restore()
            app.deviceRestrictionStore.restore()
            EnforcementController(context, app.appPolicyStore).reconcileAllUsers()
            PolicyUpdateNotifier.schedule(context)
        }
    }
}
