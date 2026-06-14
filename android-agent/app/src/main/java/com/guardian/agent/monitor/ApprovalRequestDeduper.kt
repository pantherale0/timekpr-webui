package com.guardian.agent.monitor

import com.guardian.agent.policy.ApprovalPolicy
import java.util.concurrent.ConcurrentHashMap

/**
 * Rate-limits approval request alerts so DNS retries and repeated launch attempts
 * do not flood the server pending queue.
 */
object ApprovalRequestDeduper {
    private const val COOLDOWN_MS = 5 * 60 * 1000L

    private val lastEmittedAt = ConcurrentHashMap<String, Long>()

    fun shouldEmit(requestType: String, targetValue: String): Boolean {
        val key = dedupeKey(requestType, targetValue)
        val now = System.currentTimeMillis()
        val last = lastEmittedAt[key]
        if (last != null && now - last < COOLDOWN_MS) {
            return false
        }
        lastEmittedAt[key] = now
        return true
    }

    fun clearTarget(requestType: String, targetValue: String) {
        lastEmittedAt.remove(dedupeKey(requestType, targetValue))
    }

    fun onAppApprovalPolicySynced(approval: ApprovalPolicy?) {
        approval?.approvedPackages?.forEach { packageName ->
            clearTarget("app_launch", packageName)
        }
    }

    fun onDomainGrantsSynced(allowedDomains: Collection<String>) {
        allowedDomains.forEach { domain ->
            clearTarget("domain_access", domain)
        }
    }

    private fun dedupeKey(requestType: String, targetValue: String): String {
        return "${requestType.trim().lowercase()}:${targetValue.trim().lowercase()}"
    }
}
