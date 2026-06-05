package com.timekpr.agent.enforcement

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.admin.TimeKprDeviceAdminReceiver
import com.timekpr.agent.monitor.UsageMonitorService
import com.timekpr.agent.policy.AppPolicyStore
import com.timekpr.agent.util.AndroidUsers
import com.timekpr.agent.vpn.DomainBlockVpnService

class EnforcementController(
    private val context: Context,
    private val appPolicyStore: AppPolicyStore,
) {
    private val timeLimitStore = TimeKprApplication.from(context).timeLimitStore
    private val adminComponent = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)

    fun startAll() {
        val username = AndroidUsers.currentLinuxUsername(context)
        applyTimePolicies(username)
        applyAppPolicies(username)
        DomainBlockVpnService.reconcile(context)
        UsageMonitorService.start(context)
    }

    fun applyTimePolicies(username: String) {
        val allowed = timeLimitStore.isAccessAllowed(username)
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isAdminActive(adminComponent)) return
        if (!allowed) {
            dpm.lockNow()
        }
    }

    fun applyAppPolicies(username: String) {
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isAdminActive(adminComponent)) return

        val blocked = appPolicyStore.blockedPackages(username).toTypedArray()
        if (blocked.isNotEmpty()) {
            dpm.setPackagesSuspended(adminComponent, blocked, true)
        }
    }

    fun suspendBlockedLaunch(packageName: String, username: String): Boolean {
        if (packageName !in appPolicyStore.blockedPackages(username)) {
            return false
        }
        val homeIntent = Intent(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_HOME)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK
        }
        context.startActivity(homeIntent)
        return true
    }
}
