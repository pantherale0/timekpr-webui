package com.guardian.agent.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.UserHandle
import android.util.Log
import com.guardian.agent.GuardianApplication
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.policy.PolicyStorePayloadPush
import com.guardian.agent.enforcement.EnforcementCoordinator
import com.guardian.agent.boot.SecondaryUserInitService
import com.guardian.agent.monitor.UsageMonitorService
import com.guardian.agent.service.AgentPersistentConnectionService
import com.guardian.agent.vpn.DomainBlockVpnService
import com.guardian.agent.config.AgentConfigStore

class UserSwitchedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != ACTION_USER_SWITCHED) return

        val userId = readSwitchedUserId(intent)
        Log.i(TAG, "User switched to user ${userId ?: "unknown"}")
        val app = GuardianApplication.from(context)
        val configStore = app.configStore
        if (userId != null && userId != 0 && android.os.Process.myUid() / 100_000 == 0) {
            if (configStore.load().managementMode != AgentConfigStore.MANAGEMENT_MODE_EXCLUSIVE_DO) {
                PolicyStorePayloadPush.pushToUser(context, userId)
                bootstrapSecondaryUser(context, userId)
                SecondaryUserInitService.startOnUser(context, userId)
            }
        }

        // Keep user-0 usage monitor alive for cross-profile reporting over the WebSocket.
        if (android.os.Process.myUid() / 100_000 == 0) {
            UsageMonitorService.start(context)
            AgentPersistentConnectionService.start(context)
        }

        val pendingResult = goAsync()
        EnforcementCoordinator.scheduleReconcile(context) {
            pendingResult.finish()
        }
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
