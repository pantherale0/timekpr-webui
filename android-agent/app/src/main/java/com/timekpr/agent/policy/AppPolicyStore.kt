package com.timekpr.agent.policy

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

data class AppPolicyRule(
    val applicationName: String,
    val executablePath: String,
    val matchType: String,
    val preset: String,
) {
    val packageName: String?
        get() {
            val trimmed = executablePath.trim()
            if (trimmed.isBlank()) return null
            val normalized = trimmed.removePrefix(ANDROID_PACKAGE_PREFIX)
            return normalized.takeIf { it.isNotBlank() }
        }

    companion object {
        private const val ANDROID_PACKAGE_PREFIX = "/android/package/"
    }
}

class AppPolicyStore(context: Context) {
    private val appContext = context.applicationContext
    private val prefs = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val policiesByUser = mutableMapOf<String, MutableList<AppPolicyRule>>()
    private val lastEnforcedBlockedByUser = mutableMapOf<String, Set<String>>()
    private val packagesReleasedBySync = mutableMapOf<String, Set<String>>()

    fun rulesForUser(username: String): List<AppPolicyRule> {
        return policiesByUser[username]?.toList() ?: emptyList()
    }

    fun syncPolicies(username: String, policiesArray: JSONArray) {
        val previousBlocked = blockedPackages(username)
        val rules = mutableListOf<AppPolicyRule>()
        for (index in 0 until policiesArray.length()) {
            val entry = policiesArray.optJSONObject(index) ?: continue
            val preset = entry.optString("preset")
            if (preset !in RESTRICTIVE_PRESETS) continue
            rules.add(
                AppPolicyRule(
                    applicationName = entry.optString("application_name"),
                    executablePath = entry.optString("executable_path"),
                    matchType = entry.optString("match_type", "executable"),
                    preset = preset,
                ),
            )
        }
        policiesByUser[username] = rules
        val newBlocked = rules
            .filter { it.preset == "blocked" }
            .mapNotNull { it.packageName }
            .toSet()
        packagesReleasedBySync[username] = previousBlocked - newBlocked
        persist()
    }

    fun lastEnforcedBlockedPackages(username: String): Set<String> {
        return lastEnforcedBlockedByUser[username] ?: emptySet()
    }

    fun setLastEnforcedBlockedPackages(username: String, packages: Set<String>) {
        lastEnforcedBlockedByUser[username] = packages.toSet()
        persistLastEnforcedBlocked()
    }

    fun consumePackagesReleasedBySync(username: String): Set<String> {
        val released = packagesReleasedBySync.remove(username) ?: emptySet()
        return released
    }

    fun blockedPackages(username: String): Set<String> {
        return rulesForUser(username)
            .filter { it.preset == "blocked" }
            .mapNotNull { it.packageName }
            .toSet()
    }

    fun noInternetPackages(username: String): Set<String> {
        return rulesForUser(username)
            .filter { it.preset == "no_internet" }
            .mapNotNull { it.packageName }
            .toSet()
    }

    private fun persist() {
        val root = JSONObject()
        policiesByUser.forEach { (user, rules) ->
            val array = JSONArray()
            rules.forEach { rule ->
                array.put(
                    JSONObject()
                        .put("application_name", rule.applicationName)
                        .put("executable_path", rule.executablePath)
                        .put("match_type", rule.matchType)
                        .put("preset", rule.preset),
                )
            }
            root.put(user, array)
        }
        prefs.edit().putString(KEY_RULES, root.toString()).apply()
    }

    private fun persistLastEnforcedBlocked() {
        val root = JSONObject()
        lastEnforcedBlockedByUser.forEach { (user, packages) ->
            val array = JSONArray()
            packages.forEach { array.put(it) }
            root.put(user, array)
        }
        prefs.edit().putString(KEY_LAST_ENFORCED_BLOCKED, root.toString()).apply()
    }

    private fun restoreLastEnforcedBlocked(raw: String?) {
        if (raw.isNullOrBlank()) return
        try {
            val root = JSONObject(raw)
            root.keys().forEach { user ->
                val array = root.optJSONArray(user) ?: return@forEach
                val packages = mutableSetOf<String>()
                for (index in 0 until array.length()) {
                    val packageName = array.optString(index).trim()
                    if (packageName.isNotBlank()) {
                        packages.add(packageName)
                    }
                }
                lastEnforcedBlockedByUser[user] = packages
            }
        } catch (_: Exception) {
            lastEnforcedBlockedByUser.clear()
        }
    }

    fun restore() {
        policiesByUser.clear()
        packagesReleasedBySync.clear()
        val raw = prefs.getString(KEY_RULES, null)
        if (raw != null) {
            try {
                val root = JSONObject(raw)
                root.keys().forEach { user ->
                    val array = root.optJSONArray(user) ?: return@forEach
                    policiesByUser[user] = parseRulesArray(array)
                }
            } catch (_: Exception) {
                policiesByUser.clear()
            }
        }
        restoreLastEnforcedBlocked(prefs.getString(KEY_LAST_ENFORCED_BLOCKED, null))
    }

    private fun parseRulesArray(array: JSONArray): MutableList<AppPolicyRule> {
        val rules = mutableListOf<AppPolicyRule>()
        for (index in 0 until array.length()) {
            val entry = array.optJSONObject(index) ?: continue
            val preset = entry.optString("preset")
            if (preset !in RESTRICTIVE_PRESETS) continue
            rules.add(
                AppPolicyRule(
                    applicationName = entry.optString("application_name"),
                    executablePath = entry.optString("executable_path"),
                    matchType = entry.optString("match_type", "executable"),
                    preset = preset,
                ),
            )
        }
        return rules
    }

    companion object {
        private const val PREFS_NAME = "timekpr_app_policies"
        private const val KEY_RULES = "rules"
        private const val KEY_LAST_ENFORCED_BLOCKED = "last_enforced_blocked"
        private val RESTRICTIVE_PRESETS = setOf("blocked", "no_internet", "complain")
    }
}
