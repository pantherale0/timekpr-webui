package com.timekpr.agent.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.UserHandle
import android.util.Log
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.admin.CrossUserStoreSync
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.policy.PolicyStorePayloadPush
import com.timekpr.agent.enforcement.EnforcementController
import com.timekpr.agent.boot.SecondaryUserInitService
import com.timekpr.agent.monitor.UsageMonitorService
import com.timekpr.agent.vpn.DomainBlockVpnService

class UserSwitchedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != ACTION_USER_SWITCHED) return
        Log.i(TAG, "User switched broadcast received.")

        val userId = readSwitchedUserId(intent)
        if (userId != null && userId != 0 && android.os.Process.myUid() / 100_000 == 0) {
            CrossUserStoreSync.replicateFromPrimaryToUser(context, userId)
            PolicyStorePayloadPush.pushToUser(context, userId)
            bootstrapSecondaryUser(context, userId)
            SecondaryUserInitService.startOnUser(context, userId)
        }

        val app = TimeKprApplication.from(context)
        val enforcement = EnforcementController(context, app.appPolicyStore)
        enforcement.reconcileAllUsers()
    }

    private fun readSwitchedUserId(intent: Intent): Int? {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.N) {
            val handle = intent.getParcelableExtra(Intent.EXTRA_USER, UserHandle::class.java)
            if (handle != null) {
                return try {
                    val method = UserHandle::class.java.getMethod("getIdentifier")
                    method.invoke(handle) as? Int
                } catch (_: Exception) {
                    handle.hashCode()
                }
            }
        }
        @Suppress("DEPRECATION")
        val legacyId = intent.getIntExtra("android.intent.extra.user_handle", -1)
        if (legacyId >= 0) return legacyId
        return null
    }

    private fun bootstrapSecondaryUser(context: Context, userId: Int) {
        if (!DeviceOwnerProvisioner.isDeviceOwner(context)) return
        val userHandle = userHandleForId(userId) ?: return
        try {
            val monitorIntent = Intent(context, UsageMonitorService::class.java)
            startForegroundServiceAsUser(context, monitorIntent, userHandle)
            val reloadIntent = Intent(DomainBlockVpnService.ACTION_RELOAD_POLICY)
            context.sendBroadcastAsUser(reloadIntent, userHandle)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to bootstrap secondary user $userId", e)
        }
    }

    private fun startForegroundServiceAsUser(context: Context, intent: Intent, userHandle: UserHandle) {
        try {
            val method = Context::class.java.getMethod(
                "startForegroundServiceAsUser",
                Intent::class.java,
                UserHandle::class.java,
            )
            method.invoke(context, intent, userHandle)
        } catch (e: Exception) {
            Log.w(TAG, "startForegroundServiceAsUser failed; trying startServiceAsUser", e)
            val method = Context::class.java.getMethod(
                "startServiceAsUser",
                Intent::class.java,
                UserHandle::class.java,
            )
            method.invoke(context, intent, userHandle)
        }
    }

    private fun userHandleForId(userId: Int): UserHandle? {
        return try {
            val constructor = UserHandle::class.java.getConstructor(Int::class.javaPrimitiveType)
            constructor.newInstance(userId) as UserHandle
        } catch (_: Exception) {
            null
        }
    }

    companion object {
        private const val TAG = "UserSwitchedReceiver"
        private const val ACTION_USER_SWITCHED = "android.intent.action.USER_SWITCHED"
    }
}
