package com.timekpr.agent.policy

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.UserHandle
import android.util.Log
import com.timekpr.agent.TimeKprApplication
import org.json.JSONArray

object PolicyPayloadPush {
    fun pushToUser(primaryContext: Context, targetUserId: Int, androidUserId: Int) {
        if (targetUserId == 0) return
        val app = TimeKprApplication.from(primaryContext)
        val store = app.domainPolicyStore
        store.restore()
        val policy = DomainPolicyResolver.loadVpnPolicyForUser(primaryContext, androidUserId)
        if (policy.blockedDomains.isEmpty()) return
        val userHandle = userHandleForId(targetUserId) ?: return
        val intent = Intent(PolicyPayloadReceiver.ACTION_POLICY_PAYLOAD)
            .setComponent(ComponentName(primaryContext.packageName, PolicyPayloadReceiver::class.java.name))
            .putExtra(PolicyPayloadReceiver.EXTRA_POLICY_UID, policy.policyUid)
            .putExtra(
                PolicyPayloadReceiver.EXTRA_LINUX_USERNAME,
                store.policyForUid(policy.policyUid)?.linuxUsername.orEmpty(),
            )
            .putExtra(
                PolicyPayloadReceiver.EXTRA_BLOCKED_DOMAINS,
                JSONArray(policy.blockedDomains.toList()).toString(),
            )
            .putExtra(
                PolicyPayloadReceiver.EXTRA_ALLOWED_DOMAINS,
                JSONArray(policy.allowedDomains.toList()).toString(),
            )
        try {
            primaryContext.sendBroadcastAsUser(intent, userHandle)
            Log.i(TAG, "Pushed policy payload to user $targetUserId (${policy.blockedDomains.size} domains)")
        } catch (e: Exception) {
            Log.w(TAG, "Failed to push policy payload to user $targetUserId", e)
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

    private const val TAG = "PolicyPayloadPush"
}
