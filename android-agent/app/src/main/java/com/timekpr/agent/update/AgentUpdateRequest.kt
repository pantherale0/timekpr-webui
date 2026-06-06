package com.timekpr.agent.update

import org.json.JSONObject

data class AgentUpdateRequest(
    val targetVersion: String,
    val apkUrl: String?,
    val signatureChecksum: String?,
    val updateAvailable: Boolean,
) {
    fun resolvedApkUrl(): String? {
        val direct = apkUrl?.trim().orEmpty()
        if (direct.isNotEmpty()) {
            return direct
        }
        val version = targetVersion.trim()
        if (version.isEmpty()) {
            return null
        }
        val tag = if (version.startsWith("v")) version else "v$version"
        return "https://github.com/$DEFAULT_GITHUB_REPO/releases/download/$tag/timekpr-android-agent-$tag.apk"
    }

    fun resolvedChecksumUrl(): String? {
        val version = targetVersion.trim()
        if (version.isEmpty()) {
            return null
        }
        val tag = if (version.startsWith("v")) version else "v$version"
        return "https://github.com/$DEFAULT_GITHUB_REPO/releases/download/$tag/timekpr-android-agent-$tag.signature-checksum"
    }

    fun toJson(): JSONObject {
        return JSONObject()
            .put(KEY_TARGET_VERSION, targetVersion)
            .put(KEY_APK_URL, apkUrl.orEmpty())
            .put(KEY_SIGNATURE_CHECKSUM, signatureChecksum.orEmpty())
            .put(KEY_UPDATE_AVAILABLE, updateAvailable)
    }

    companion object {
        private const val KEY_TARGET_VERSION = "target_version"
        private const val KEY_APK_URL = "apk_url"
        private const val KEY_SIGNATURE_CHECKSUM = "signature_checksum"
        private const val KEY_UPDATE_AVAILABLE = "update_available"

        const val DEFAULT_GITHUB_REPO = "pantherale0/timekpr-webui"

        fun from(message: JSONObject): AgentUpdateRequest {
            return AgentUpdateRequest(
                targetVersion = message.optString(KEY_TARGET_VERSION).trim(),
                apkUrl = message.optString(KEY_APK_URL).trim().ifBlank { null },
                signatureChecksum = message.optString(KEY_SIGNATURE_CHECKSUM).trim().ifBlank { null },
                updateAvailable = message.optBoolean(KEY_UPDATE_AVAILABLE, false),
            )
        }

        fun fromJson(raw: String): AgentUpdateRequest? {
            return try {
                from(JSONObject(raw))
            } catch (_: Exception) {
                null
            }
        }
    }
}
