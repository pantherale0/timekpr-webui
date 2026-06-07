package com.timekpr.agent

import android.app.Application
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.config.AgentConfigStore
import com.timekpr.agent.policy.AppPolicyStore
import com.timekpr.agent.policy.DeviceRestrictionStore
import com.timekpr.agent.policy.DomainPolicyStore
import com.timekpr.agent.policy.TimeLimitStore
import com.timekpr.agent.policy.PolicyIpcServer
import com.timekpr.agent.util.DirectBootHelper

class TimeKprApplication : Application() {
    lateinit var configStore: AgentConfigStore
        private set

    val timeLimitStore: TimeLimitStore by lazy { TimeLimitStore(this) }
    val domainPolicyStore: DomainPolicyStore by lazy { DomainPolicyStore(this).also { it.restore() } }
    val appPolicyStore: AppPolicyStore by lazy { AppPolicyStore(this).also { it.restore() } }
    val deviceRestrictionStore: DeviceRestrictionStore by lazy { DeviceRestrictionStore(this).also { it.restore() } }
    val policyIpcServer: PolicyIpcServer by lazy { PolicyIpcServer(this) }

    override fun onCreate() {
        super.onCreate()
        configStore = AgentConfigStore(this)

        if (DirectBootHelper.isCredentialStorageUnlocked(this)) {
            configStore.migrateToDeviceProtectedStorageIfNeeded()
            if ((android.os.Process.myUid() / 100_000) == 0) {
                policyIpcServer.start()
                com.timekpr.agent.admin.SecondaryUserProvisioner.ensurePrimaryUiVisible(this)
            }
            DeviceOwnerProvisioner.applyIfDeviceOwner(this)
        } else if ((android.os.Process.myUid() / 100_000) == 0) {
            policyIpcServer.start()
            DeviceOwnerProvisioner.applyIfDeviceOwner(this)
        }
    }

    companion object {
        fun from(context: android.content.Context): TimeKprApplication {
            return context.applicationContext as TimeKprApplication
        }
    }
}
