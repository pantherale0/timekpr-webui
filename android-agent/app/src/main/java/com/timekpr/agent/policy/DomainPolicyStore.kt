package com.timekpr.agent.policy

import android.content.Context
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

data class UidPolicy(
    val linuxUsername: String,
    val sourceIds: List<String>,
)

class DomainPolicyStore(context: Context) {
    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    val sources = ConcurrentHashMap<String, MutableSet<String>>()
    val sourceRevisions = ConcurrentHashMap<String, String>()
    val policies = ConcurrentHashMap<String, UidPolicy>()
    val syncSessions = ConcurrentHashMap<String, SyncSession>()

    data class SyncSession(
        val sources: MutableMap<String, SourceEntry>,
        val policies: MutableMap<String, UidPolicy>,
    )

    data class SourceEntry(
        var revision: String,
        val domains: MutableSet<String>,
    )

    fun getStatePayload(): JSONObject {
        val revisions = JSONObject()
        sourceRevisions.forEach { (id, revision) -> revisions.put(id, revision) }
        return JSONObject()
            .put("source_revisions", revisions)
            .put("policy_count", policies.size)
            .put("source_count", sources.size)
    }

    fun blockedDomainsForUid(linuxUid: String): Set<String> {
        val policy = policies[linuxUid] ?: return emptySet()
        val blocked = mutableSetOf<String>()
        policy.sourceIds.forEach { sourceId ->
            sources[sourceId]?.let { blocked.addAll(it) }
        }
        return blocked
    }

    fun allBlockedDomains(): Set<String> {
        if (policies.isEmpty()) {
            return sources.values.flatten().toSet()
        }
        return policies.keys.flatMap { blockedDomainsForUid(it) }.toSet()
    }

    fun applyFullSync(payload: JSONObject) {
        sources.clear()
        sourceRevisions.clear()
        policies.clear()

        val sourcesObj = payload.optJSONObject("sources") ?: JSONObject()
        sourcesObj.keys().forEach { sourceId ->
            val domains = sourcesObj.optJSONArray(sourceId) ?: return@forEach
            val normalized = mutableSetOf<String>()
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
        persist()
        return true
    }

    fun persist() {
        val snapshot = JSONObject()
            .put("sources", JSONObject(sources.mapValues { (_, domains) ->
                org.json.JSONArray(domains.toList())
            }))
            .put("source_revisions", JSONObject(sourceRevisions.toMap()))
            .put(
                "policies",
                JSONObject(policies.mapValues { (_, policy) ->
                    JSONObject()
                        .put("linux_username", policy.linuxUsername)
                        .put("source_ids", org.json.JSONArray(policy.sourceIds))
                }),
            )
        prefs.edit().putString(KEY_SNAPSHOT, snapshot.toString()).apply()
    }

    fun restore() {
        val raw = prefs.getString(KEY_SNAPSHOT, null) ?: return
        try {
            val json = JSONObject(raw)
            val payload = JSONObject()
                .put("sources", json.optJSONObject("sources") ?: JSONObject())
                .put("policies", json.optJSONObject("policies") ?: JSONObject())
            applyFullSync(payload)
            json.optJSONObject("source_revisions")?.let { revisions ->
                revisions.keys().forEach { sourceId ->
                    sourceRevisions[sourceId] = revisions.optString(sourceId)
                }
            }
        } catch (_: Exception) {
            // Ignore corrupt snapshots.
        }
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

    private fun stableRevision(domains: Set<String>): String {
        val digest = java.security.MessageDigest.getInstance("SHA-256")
        val payload = domains.sorted().joinToString("\n")
        return digest.digest(payload.toByteArray()).joinToString("") { "%02x".format(it) }
    }

    companion object {
        private const val PREFS_NAME = "timekpr_domain_policy"
        private const val KEY_SNAPSHOT = "snapshot"
    }
}
