package com.guardian.agent

import android.app.Application
import io.sentry.android.core.SentryAndroid
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.config.AgentConfigStore
import com.guardian.agent.policy.AppPolicyStore
import com.guardian.agent.policy.DeviceRestrictionStore
import com.guardian.agent.policy.DomainPolicyStore
import com.guardian.agent.policy.TimeLimitStore
import com.guardian.agent.policy.PolicyIpcServer
import com.guardian.agent.telemetry.AgentTelemetryRouter
import com.guardian.agent.util.DirectBootHelper
import com.guardian.agent.work.TelemetryFlushWorker

class GuardianApplication : Application() {
    lateinit var configStore: AgentConfigStore
        private set

    val timeLimitStore: TimeLimitStore by lazy { TimeLimitStore(this) }
    val domainPolicyStore: DomainPolicyStore by lazy { DomainPolicyStore(this).also { it.restore() } }
    val appPolicyStore: AppPolicyStore by lazy { AppPolicyStore(this).also { it.restore() } }
    val deviceRestrictionStore: DeviceRestrictionStore by lazy { DeviceRestrictionStore(this).also { it.restore() } }
    val telemetryRouter: AgentTelemetryRouter by lazy { AgentTelemetryRouter(this) }
    val policyIpcServer: PolicyIpcServer by lazy { PolicyIpcServer(this, telemetryRouter) }

    override fun onCreate() {
        super.onCreate()
        val sentryDsn = BuildConfig.SENTRY_DSN
        if (sentryDsn.isNotEmpty()) {
            SentryAndroid.init(this) { options ->
                options.dsn = sentryDsn
            }
            try {
                uniffi.guardian_agent.initNativeSentry()
            } catch (e: Exception) {
                android.util.Log.e("GuardianApplication", "Failed to initialize native Sentry", e)
            }
        }
        configStore = AgentConfigStore(this)

        if (DirectBootHelper.isCredentialStorageUnlocked(this)) {
            configStore.migrateToDeviceProtectedStorageIfNeeded()
            if ((android.os.Process.myUid() / 100_000) == 0) {
                policyIpcServer.start()
                TelemetryFlushWorker.enqueue(this)
                com.guardian.agent.admin.SecondaryUserProvisioner.ensurePrimaryUiVisible(this)
            }
            DeviceOwnerProvisioner.applyIfDeviceOwner(this)
        } else if ((android.os.Process.myUid() / 100_000) == 0) {
            policyIpcServer.start()
            TelemetryFlushWorker.enqueue(this)
            DeviceOwnerProvisioner.applyIfDeviceOwner(this)
        }
    }

    companion object {
        fun from(context: android.content.Context): GuardianApplication {
            return context.applicationContext as GuardianApplication
        }
    }
}
