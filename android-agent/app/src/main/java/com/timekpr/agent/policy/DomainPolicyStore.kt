package com.timekpr.agent.policy

import android.content.Context
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

data class UidPolicy(
    val linuxUsername: String,
    val sourceIds: List<String>,
)

class DomainPolicyStore(context: Context) {
    private val persistence = DomainPolicyPersistence(context.applicationContext)

    val sources = ConcurrentHashMap<String, MutableSet<String>>()
    val sourceRevisions = ConcurrentHashMap<String, String>()
    val policies = ConcurrentHashMap<String, UidPolicy>()
    val syncSessions = ConcurrentHashMap<String, SyncSession>()

    @Volatile
    private var blockedMatcher: BlockedDomainMatcher = BlockedDomainMatcher.EMPTY

    data class SyncSession(
        val sources: MutableMap<String, SourceEntry>,
        val policies: MutableMap<String, UidPolicy>,
    )

    data class SourceEntry(
        var revision: String,
        val domains: MutableSet<String>,
    )

    fun blockedMatcher(): BlockedDomainMatcher = blockedMatcher

    fun blockedDomainCount(): Int = blockedMatcher.domainCount

    fun getStatePayload(): JSONObject {
        val revisions = JSONObject()
        sourceRevisions.forEach { (id, revision) -> revisions.put(id, revision) }
        return JSONObject()
            .put("source_revisions", revisions)
            .put("policy_count", policies.size)
            .put("source_count", sources.size)
            .put("blocked_domain_count", blockedMatcher.domainCount)
    }

    fun blockedDomainsForUid(linuxUid: String): Set<String> {
        val policy = policies[linuxUid] ?: return emptySet()
        val blocked = HashSet<String>()
        policy.sourceIds.forEach { sourceId ->
            sources[sourceId]?.let { blocked.addAll(it) }
        }
        return blocked
    }

    fun allBlockedDomains(): Set<String> = collectEffectiveDomains()

    fun applyFullSync(payload: JSONObject) {
        sources.clear()
        sourceRevisions.clear()
        policies.clear()

        val sourcesObj = payload.optJSONObject("sources") ?: JSONObject()
        sourcesObj.keys().forEach { sourceId ->
            val domains = sourcesObj.optJSONArray(sourceId) ?: return@forEach
            val normalized = HashSet<String>(domains.length())
            for (index in 0 until domains.length()) {
                val domain = domains.optString(index).trim().lowercase().trimEnd('.')
                if (domain.isNotEmpty()) normalized.add(domain)
            }
            sources[sourceId] = normalized
            sourceRevisions[sourceId] = stableRevision(normalized)
        }

        val policiesObj = payload.optJSONObject("policies") ?: JSONObject()
        policiesObj.keys().forEach { uid ->
            val entry = policiesObj.optJSONObject(uid) ?: return@forEach
            policies[uid] = UidPolicy(
                linuxUsername = entry.optString("linux_username"),
                sourceIds = entry.optJSONArray("source_ids")?.let { array ->
                    (0 until array.length()).map { array.optString(it) }
                } ?: emptyList(),
            )
        }
        rebuildBlockedMatcher()
        persist()
    }

    fun beginSync(syncId: String) {
        val sessionSources = sources.mapValues { (sourceId, domains) ->
            SourceEntry(
                revision = sourceRevisions[sourceId] ?: "",
                domains = domains.toMutableSet(),
            )
        }.toMutableMap()
        syncSessions[syncId] = SyncSession(
            sources = sessionSources,
            policies = policies.mapValues { it.value.copy() }.toMutableMap(),
        )
    }

    fun finalizeSync(syncId: String): Boolean {
        val session = syncSessions.remove(syncId) ?: return false
        sources.clear()
        sourceRevisions.clear()
        session.sources.forEach { (sourceId, entry) ->
            sources[sourceId] = entry.domains
            sourceRevisions[sourceId] = entry.revision.ifBlank { stableRevision(entry.domains) }
        }
        policies.clear()
        policies.putAll(session.policies)
        rebuildBlockedMatcher()
        persist()
        return true
    }

    fun persist() {
        persistence.persist(
            sources = sources.mapValues { it.value.toSet() },
            sourceRevisions = sourceRevisions.toMap(),
            policies = policies.toMap(),
        )
    }

    fun restore() {
        if (!persistence.restore(
            onSources = { loaded ->
                sources.clear()
                sources.putAll(loaded)
            },
            onRevisions = { loaded ->
                sourceRevisions.clear()
                sourceRevisions.putAll(loaded)
            },
            onPolicies = { loaded ->
                policies.clear()
                policies.putAll(loaded)
            },
        )) {
            return
        }
        rebuildBlockedMatcher()
    }

    fun isDomainBlocked(domain: String, blockedSet: Set<String>): Boolean {
        val normalized = domain.trim().lowercase().trimEnd('.')
        if (normalized.isEmpty()) return false
        var candidate = normalized
        while (true) {
            if (candidate in blockedSet) return true
            val dot = candidate.indexOf('.')
            if (dot < 0) break
            candidate = candidate.substring(dot + 1)
        }
        return false
    }

    private fun rebuildBlockedMatcher() {
        blockedMatcher = BlockedDomainMatcher.from(collectEffectiveDomains())
    }

    private fun collectEffectiveDomains(): Set<String> {
        if (policies.isEmpty()) {
            if (sources.isEmpty()) return emptySet()
            val merged = HashSet<String>()
            sources.values.forEach { merged.addAll(it) }
            return merged
        }
        val merged = HashSet<String>()
        policies.values.forEach { policy ->
            policy.sourceIds.forEach { sourceId ->
                sources[sourceId]?.let { merged.addAll(it) }
            }
        }
        return merged
    }

    private fun stableRevision(domains: Collection<String>): String {
        val digest = java.security.MessageDigest.getInstance("SHA-256")
        digest.update(domains.size.toString().toByteArray())
        if (domains.size <= 10_000) {
            domains.sorted().forEach { domain ->
                digest.update(domain.toByteArray())
                digest.update(0)
            }
        } else {
            domains.forEach { domain ->
                digest.update(domain.toByteArray())
                digest.update(0)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
