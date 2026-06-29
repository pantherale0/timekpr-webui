package com.guardian.agent.admin

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import com.guardian.agent.GuardianApplication
import com.guardian.agent.config.AgentConfigStore
import com.guardian.agent.enforcement.EnforcementController
import com.guardian.agent.monitor.UsageMonitorService
import com.guardian.agent.util.AndroidUsers
import com.guardian.agent.vpn.DomainBlockVpnService
import org.json.JSONArray
import org.json.JSONObject

class DeviceLifecycleManager(private val context: Context) {
    private val app = GuardianApplication.from(context)
    private val configStore: AgentConfigStore = app.configStore

    fun unenrollLocally(): Pair<Boolean, String> {
        val username = AndroidUsers.currentLinuxUsername(context)

        app.domainPolicyStore.applyFullSync(
            JSONObject()
                .put("sources", JSONObject())
                .put("policies", JSONObject()),
        )
        app.domainPolicyStore.persist()

        app.appPolicyStore.syncPolicies(username, JSONArray(), null)
        app.deviceRestrictionStore.syncPolicy(username, null)

        DeviceOwnerProvisioner.clearVpnAuthorization(context)
        EnforcementController(context, app.appPolicyStore).applyAppPolicies(username)
        context.stopService(Intent(context, UsageMonitorService::class.java))
        DomainBlockVpnService.reconcile(context)
        configStore.clearEnrollmentState()

        return true to "Device unenrolled locally"
    }

    fun factoryReset(): Pair<Boolean, String> {
        if (!DeviceOwnerProvisioner.isDeviceOwner(context)) {
            return false to "Device owner provisioning is required for factory reset"
        }

        val dpm = context.getSystemService(DevicePolicyManager::class.java)
            ?: return false to "Device policy manager unavailable"
        val admin = ComponentName(context, GuardianDeviceAdminReceiver::class.java)
        if (!dpm.isAdminActive(admin)) {
            return false to "Device admin is not active"
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            dpm.wipeDevice(0)
        } else {
            @Suppress("DEPRECATION")
            dpm.wipeData(0)
        }
        return true to "Factory reset initiated"
    }
}
