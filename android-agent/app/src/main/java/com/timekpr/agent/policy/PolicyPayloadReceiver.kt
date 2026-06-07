package com.timekpr.agent.policy

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.vpn.DomainBlockVpnService
import org.json.JSONArray

/** Receives domain policy pushed from the primary user when cross-user storage sync fails. */
class PolicyPayloadReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != ACTION_POLICY_PAYLOAD) return
        val blockedArray = intent.getStringExtra(EXTRA_BLOCKED_DOMAINS) ?: return
        val allowedArray = intent.getStringExtra(EXTRA_ALLOWED_DOMAINS) ?: "[]"
        val policyUid = intent.getStringExtra(EXTRA_POLICY_UID) ?: return
        val linuxUsername = intent.getStringExtra(EXTRA_LINUX_USERNAME).orEmpty()
        try {
            val blocked = parseDomains(JSONArray(blockedArray))
            val allowed = parseDomains(JSONArray(allowedArray))
            val app = TimeKprApplication.from(context)
            val store = app.domainPolicyStore
            val sourceId = "payload"
            store.sources[sourceId] = blocked.toMutableSet()
            store.sourceRevisions[sourceId] = blocked.hashCode().toString()
            store.policies[policyUid] = UidPolicy(
                linuxUsername = linuxUsername,
                sourceIds = listOf(sourceId),
                allowedDomains = allowed,
            )
            store.persist()
            store.restore()
            Log.i(TAG, "Applied pushed policy uid=$policyUid blocked=${blocked.size}")
            DomainBlockVpnService.reconcile(context)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to apply pushed policy payload", e)
        }
    }

    private fun parseDomains(array: JSONArray): Set<String> {
        val domains = linkedSetOf<String>()
        for (index in 0 until array.length()) {
            val domain = array.optString(index).trim().lowercase().trimEnd('.')
            if (domain.isNotEmpty()) domains.add(domain)
        }
        return domains
    }

    companion object {
        private const val TAG = "PolicyPayloadReceiver"
        const val ACTION_POLICY_PAYLOAD = "com.timekpr.agent.policy.ACTION_POLICY_PAYLOAD"
        const val EXTRA_POLICY_UID = "policy_uid"
        const val EXTRA_LINUX_USERNAME = "linux_username"
        const val EXTRA_BLOCKED_DOMAINS = "blocked_domains"
        const val EXTRA_ALLOWED_DOMAINS = "allowed_domains"
    }
}
