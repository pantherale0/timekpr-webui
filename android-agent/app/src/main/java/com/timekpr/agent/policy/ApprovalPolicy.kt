package com.timekpr.agent.policy

import org.json.JSONArray
import org.json.JSONObject

data class ApprovalPolicy(
    val appLaunchMode: String,
    val approvedPackages: Set<String>,
    val blockedPackages: Set<String>,
) {
    companion object {
        fun parse(json: JSONObject?): ApprovalPolicy? {
            if (json == null) return null
            val mode = json.optString("app_launch_mode").trim().lowercase()
            if (mode !in SUPPORTED_MODES) return null
            return ApprovalPolicy(
                appLaunchMode = mode,
                approvedPackages = parsePackageArray(json.optJSONArray("approved_packages")),
                blockedPackages = parsePackageArray(json.optJSONArray("blocked_packages")),
            )
        }

        fun effectiveBlockedPackages(
            rulesBlocked: Set<String>,
            approval: ApprovalPolicy?,
        ): Set<String> {
            if (approval == null) return rulesBlocked
            return approval.blockedPackages - approval.approvedPackages
        }

        private val SUPPORTED_MODES = setOf("allowlist", "blocklist")

        private fun parsePackageArray(array: JSONArray?): Set<String> {
            if (array == null) return emptySet()
            val packages = LinkedHashSet<String>()
            for (index in 0 until array.length()) {
                val packageName = array.optString(index).trim()
                if (packageName.isNotBlank()) {
                    packages.add(packageName)
                }
            }
            return packages
        }
    }
}
