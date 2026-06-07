package com.timekpr.agent

import android.app.Application
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.config.AgentConfigStore
import com.timekpr.agent.policy.AppPolicyStore
import com.timekpr.agent.policy.DeviceRestrictionStore
import com.timekpr.agent.policy.DomainPolicyStore
import com.timekpr.agent.policy.TimeLimitStore
import com.timekpr.agent.policy.PolicyIpcServer

class TimeKprApplication : Application() {
    lateinit var configStore: AgentConfigStore
        private set
    lateinit var timeLimitStore: TimeLimitStore
        private set
    lateinit var domainPolicyStore: DomainPolicyStore
        private set
    lateinit var appPolicyStore: AppPolicyStore
        private set
    lateinit var deviceRestrictionStore: DeviceRestrictionStore
        private set
    lateinit var policyIpcServer: PolicyIpcServer
        private set

    override fun onCreate() {
        super.onCreate()
        configStore = AgentConfigStore(this)
        timeLimitStore = TimeLimitStore(this)
        domainPolicyStore = DomainPolicyStore(this).also { it.restore() }
        appPolicyStore = AppPolicyStore(this).also { it.restore() }
        deviceRestrictionStore = DeviceRestrictionStore(this).also { it.restore() }
        policyIpcServer = PolicyIpcServer(this)
        
        val myUid = android.os.Process.myUid()
        val isUserZero = (myUid / 100000) == 0
        if (isUserZero) {
            policyIpcServer.start()
            com.timekpr.agent.admin.SecondaryUserProvisioner.ensurePrimaryUiVisible(this)
        } else {
            com.timekpr.agent.admin.SecondaryUserProvisioner.prepareAtLaunch(this)
        }
        
        DeviceOwnerProvisioner.applyIfDeviceOwner(this)
    }

    companion object {
        fun from(context: android.content.Context): TimeKprApplication {
            return context.applicationContext as TimeKprApplication
        }
    }
}
