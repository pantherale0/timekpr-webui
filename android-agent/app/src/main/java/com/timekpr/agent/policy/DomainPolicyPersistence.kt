package com.timekpr.agent.policy

import android.content.Context
import org.json.JSONObject
import java.io.BufferedReader
import java.io.BufferedWriter
import java.io.File

/**
 * Stores large domain lists on disk instead of bloating SharedPreferences JSON.
 */
internal class DomainPolicyPersistence(context: Context) {
    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val sourcesDir = File(context.filesDir, "domain_policy/sources").apply { mkdirs() }

    fun persist(
        sources: Map<String, Set<String>>,
        sourceRevisions: Map<String, String>,
        policies: Map<String, UidPolicy>,
    ) {
        val activeSourceIds = sources.keys.toSet()
        sourcesDir.listFiles()?.forEach { file ->
            if (file.name.removeSuffix(".lst") !in activeSourceIds) {
                file.delete()
            }
        }

        for ((sourceId, domains) in sources) {
            writeSourceFile(sourceId, domains)
        }

        val meta = JSONObject()
            .put("source_ids", org.json.JSONArray(activeSourceIds.toList()))
            .put("source_revisions", JSONObject(sourceRevisions.toMap()))
            .put(
                "policies",
                JSONObject(policies.mapValues { (_, policy) ->
                    val entry = JSONObject()
                        .put("linux_username", policy.linuxUsername)
                        .put("source_ids", org.json.JSONArray(policy.sourceIds))
                    if (policy.allowedDomains.isNotEmpty()) {
                        entry.put("allowed_domains", org.json.JSONArray(policy.allowedDomains.toList()))
                    }
                    if (policy.domainAccessMode != UidPolicy.DOMAIN_ACCESS_BLOCKLIST_ONLY) {
                        entry.put("domain_access_mode", policy.domainAccessMode)
                    }
                    entry
                }),
            )
        prefs.edit().putString(KEY_META, meta.toString()).apply()
    }

    fun restore(
        onSources: (Map<String, MutableSet<String>>) -> Unit,
        onRevisions: (Map<String, String>) -> Unit,
        onPolicies: (Map<String, UidPolicy>) -> Unit,
    ): Boolean {
        val raw = prefs.getString(KEY_META, null) ?: return false
        return try {
            val meta = JSONObject(raw)
            val sourceIds = meta.optJSONArray("source_ids") ?: org.json.JSONArray()
            val loadedSources = linkedMapOf<String, MutableSet<String>>()
            for (index in 0 until sourceIds.length()) {
                val sourceId = sourceIds.optString(index)
                if (sourceId.isNotBlank()) {
                    loadedSources[sourceId] = readSourceFile(sourceId)
                }
            }
            onSources(loadedSources)

            val revisions = linkedMapOf<String, String>()
            meta.optJSONObject("source_revisions")?.let { json ->
                json.keys().forEach { sourceId ->
                    revisions[sourceId] = json.optString(sourceId)
                }
            }
            onRevisions(revisions)

            val policies = linkedMapOf<String, UidPolicy>()
            meta.optJSONObject("policies")?.let { json ->
                json.keys().forEach { uid ->
                    val entry = json.optJSONObject(uid) ?: return@forEach
                    policies[uid] = DomainPolicyStore.parseUidPolicyEntry(entry)
                }
            }
            onPolicies(policies)
            true
        } catch (_: Exception) {
            false
        }
    }

    private fun sourceFile(sourceId: String): File {
        val safeName = sourceId.replace(Regex("[^a-zA-Z0-9._-]"), "_")
        return File(sourcesDir, "$safeName.lst")
    }

    private fun writeSourceFile(sourceId: String, domains: Collection<String>) {
        val file = sourceFile(sourceId)
        BufferedWriter(file.writer()).use { writer ->
            for (domain in domains) {
                writer.write(domain)
                writer.newLine()
            }
        }
    }

    private fun readSourceFile(sourceId: String): MutableSet<String> {
        val file = sourceFile(sourceId)
        if (!file.exists()) return mutableSetOf()
        val domains = HashSet<String>((file.length() / 16).toInt().coerceAtLeast(16))
        BufferedReader(file.reader()).use { reader ->
            reader.lineSequence().forEach { line ->
                val domain = line.trim().lowercase().trimEnd('.')
                if (domain.isNotEmpty()) domains.add(domain)
            }
        }
        return domains
    }

    companion object {
        private const val PREFS_NAME = "timekpr_domain_policy"
        private const val KEY_META = "meta"
    }
}
