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
        get() = when (matchType) {
            "package" -> executablePath.trim()
            else -> executablePath.removePrefix("/android/package/").takeIf { it.isNotBlank() }
        }
}

class AppPolicyStore(context: Context) {
    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val policiesByUser = mutableMapOf<String, MutableList<AppPolicyRule>>()

    fun rulesForUser(username: String): List<AppPolicyRule> {
        return policiesByUser[username]?.toList() ?: emptyList()
    }

    fun syncPolicies(username: String, policiesArray: JSONArray) {
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
        persist()
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

    fun restore() {
        val raw = prefs.getString(KEY_RULES, null) ?: return
        try {
            val root = JSONObject(raw)
            policiesByUser.clear()
            root.keys().forEach { user ->
                val array = root.optJSONArray(user) ?: return@forEach
                syncPolicies(user, array)
            }
        } catch (_: Exception) {
            policiesByUser.clear()
        }
    }

    companion object {
        private const val PREFS_NAME = "timekpr_app_policies"
        private const val KEY_RULES = "rules"
        private val RESTRICTIVE_PRESETS = setOf("blocked", "no_internet", "complain")
    }
}
