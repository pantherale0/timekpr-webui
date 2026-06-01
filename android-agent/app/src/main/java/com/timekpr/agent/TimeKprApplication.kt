package com.timekpr.agent

import android.app.Application
import com.timekpr.agent.config.AgentConfigStore
import com.timekpr.agent.policy.DomainPolicyStore
import com.timekpr.agent.policy.TimeLimitStore

class TimeKprApplication : Application() {
    lateinit var configStore: AgentConfigStore
        private set
    lateinit var timeLimitStore: TimeLimitStore
        private set
    lateinit var domainPolicyStore: DomainPolicyStore
        private set

    override fun onCreate() {
        super.onCreate()
        configStore = AgentConfigStore(this)
        timeLimitStore = TimeLimitStore(this)
        domainPolicyStore = DomainPolicyStore(this)
    }

    companion object {
        fun from(context: android.content.Context): TimeKprApplication {
            return context.applicationContext as TimeKprApplication
        }
    }
}
