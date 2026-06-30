package com.guardian.agent.update

import org.json.JSONObject

data class AgentUpdateRequest(
    val targetVersion: String,
    val githubRepo: String?,
    val apkUrl: String?,
    val signatureChecksum: String?,
    val downloadUrl: String?,
    val checksumUrl: String?,
    val updateAvailable: Boolean,
) {
    fun resolvedApkUrl(): String? {
        val direct = apkUrl?.trim().orEmpty()
        if (direct.isNotEmpty()) {
            return direct
        }
        val repo = githubRepo?.trim().orEmpty()
        val version = targetVersion.trim()
        if (repo.isEmpty() || version.isEmpty()) {
            return null
        }
        val tag = if (version.startsWith("v")) version else "v$version"
        return "https://github.com/$repo/releases/download/$tag/guardian-android-agent-$tag.apk"
    }

    fun resolvedChecksumUrl(): String? {
        val apk = apkUrl?.trim().orEmpty()
        if (apk.endsWith(".apk", ignoreCase = true)) {
            return apk.dropLast(4) + ".signature-checksum"
        }
        val repo = githubRepo?.trim().orEmpty()
        val version = targetVersion.trim()
        if (repo.isEmpty() || version.isEmpty()) {
            return null
        }
        val tag = if (version.startsWith("v")) version else "v$version"
        return "https://github.com/$repo/releases/download/$tag/guardian-android-agent-$tag.signature-checksum"
    }

    fun toJson(): JSONObject {
        return JSONObject()
            .put(KEY_TARGET_VERSION, targetVersion)
            .put(KEY_GITHUB_REPO, githubRepo.orEmpty())
            .put(KEY_APK_URL, apkUrl.orEmpty())
            .put(KEY_SIGNATURE_CHECKSUM, signatureChecksum.orEmpty())
            .put(KEY_DOWNLOAD_URL, downloadUrl.orEmpty())
            .put(KEY_CHECKSUM_URL, checksumUrl.orEmpty())
            .put(KEY_UPDATE_AVAILABLE, updateAvailable)
    }

    companion object {
        private const val KEY_TARGET_VERSION = "target_version"
        private const val KEY_GITHUB_REPO = "github_repo"
        private const val KEY_APK_URL = "apk_url"
        private const val KEY_SIGNATURE_CHECKSUM = "signature_checksum"
        private const val KEY_DOWNLOAD_URL = "download_url"
        private const val KEY_CHECKSUM_URL = "checksum_url"
        private const val KEY_UPDATE_AVAILABLE = "update_available"

        fun from(message: JSONObject): AgentUpdateRequest {
            return AgentUpdateRequest(
                targetVersion = message.optString(KEY_TARGET_VERSION).trim(),
                githubRepo = message.optString(KEY_GITHUB_REPO).trim().ifBlank { null },
                apkUrl = message.optString(KEY_APK_URL).trim().ifBlank { null },
                signatureChecksum = message.optString(KEY_SIGNATURE_CHECKSUM).trim().ifBlank { null },
                downloadUrl = message.optString(KEY_DOWNLOAD_URL).trim().ifBlank { null },
                checksumUrl = message.optString(KEY_CHECKSUM_URL).trim().ifBlank { null },
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
