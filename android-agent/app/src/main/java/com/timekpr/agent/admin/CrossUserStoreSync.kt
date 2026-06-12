package com.timekpr.agent.admin

import android.content.Context
import android.os.Process
import com.timekpr.agent.util.AgentLog
import com.timekpr.agent.policy.PolicyStorePayloadPush
import com.timekpr.agent.policy.ProfileProvisioningStore
import com.timekpr.agent.util.AndroidUsers
import java.io.File

/**
 * Replicates enrollment and policy state from the primary user (User 0) to managed
 * secondary users. Android isolates app data per user, so secondary profiles need an
 * explicit copy to share the same device identity and enforcement rules.
 */
object CrossUserStoreSync {
    private const val TAG = "CrossUserStoreSync"

    private val REPLICATED_PREFS = listOf(
        "timekpr_agent_config",
        "timekpr_app_policies",
        "timekpr_time_limits",
        "timekpr_domain_policy",
        "timekpr_device_restrictions",
        "timekpr_enforcement",
        "timekpr_profile_provisioning",
    )

    fun replicatedPrefNames(): List<String> = REPLICATED_PREFS

    private val REPLICATED_FILE_DIRS = listOf(
        "domain_policy",
    )

    fun pushPolicyToAllSecondaryUsers(primaryContext: Context) {
        if (currentUserId(primaryContext) != 0) return
        secondaryTargetUserIds(primaryContext).forEach { userId ->
            replicateFromPrimaryToUser(primaryContext, userId)
            PolicyStorePayloadPush.pushToUser(primaryContext, userId)
        }
    }

    /** @deprecated Prefer [pushPolicyToAllSecondaryUsers]; direct file copy requires the target data dir. */
    fun replicateToAllSecondaryUsers(primaryContext: Context) {
        pushPolicyToAllSecondaryUsers(primaryContext)
    }

    private fun secondaryTargetUserIds(context: Context): List<Int> {
        val fromProvisioning = ProfileProvisioningStore(context).allProvisionedUserIds().filter { it > 0 }
        if (fromProvisioning.isNotEmpty()) return fromProvisioning
        return AndroidUsers.linuxUsersPayload(context)
            .mapNotNull { (it["uid"] as? Number)?.toInt() }
            .filter { it > 0 }
    }

    fun replicateFromPrimaryToCurrentUser(secondaryContext: Context): Boolean {
        val userId = currentUserId(secondaryContext)
        if (userId == 0) return true
        val primaryContext = AndroidUsers.getUserContext(secondaryContext, 0) ?: return false
        return replicateFromPrimaryToUser(primaryContext, userId)
    }

    fun replicateFromPrimaryToUser(primaryContext: Context, targetUserId: Int): Boolean {
        if (targetUserId == 0) return true
        val targetContext = AndroidUsers.getUserContext(primaryContext, targetUserId)
        if (targetContext == null) {
            AgentLog.d(TAG, "Could not open context for user $targetUserId")
            return false
        }
        if (!File(targetContext.applicationInfo.dataDir).exists()) {
            return false
        }
        try {
            for (prefsName in REPLICATED_PREFS) {
                copySharedPreferences(primaryContext, targetContext, prefsName)
            }
            for (dirName in REPLICATED_FILE_DIRS) {
                try {
                    copyFilesDirectory(primaryContext, targetContext, dirName)
                } catch (e: Exception) {
                    AgentLog.d(TAG, "Failed to copy files dir $dirName to user $targetUserId: ${e.message}")
                }
            }
            AgentLog.d(TAG, "Replicated stores from user 0 to user $targetUserId")
            return true
        } catch (e: Exception) {
            AgentLog.wOnce(TAG, "replicate_$targetUserId", "Failed to replicate stores to user $targetUserId: ${e.message}")
            return false
        }
    }

    private fun copySharedPreferences(from: Context, to: Context, name: String) {
        if (from.applicationInfo.dataDir != to.applicationInfo.dataDir) {
            copySharedPreferencesCrossUser(from, to, name)
            return
        }

        val sourceFile = File(from.applicationInfo.dataDir, "shared_prefs/$name.xml")
        if (!sourceFile.exists()) {
            val source = from.getSharedPreferences(name, Context.MODE_PRIVATE)
            if (source.all.isEmpty()) return
        }
        val targetDir = File(to.applicationInfo.dataDir, "shared_prefs")
        targetDir.mkdirs()
        val targetFile = File(targetDir, "$name.xml")
        if (sourceFile.exists()) {
            sourceFile.copyTo(targetFile, overwrite = true)
        } else {
            copySharedPreferencesCrossUser(from, to, name)
        }
    }

    private fun copySharedPreferencesCrossUser(from: Context, to: Context, name: String) {
        to.getSharedPreferences("_timekpr_sync_bootstrap", Context.MODE_PRIVATE)
        val source = from.getSharedPreferences(name, Context.MODE_PRIVATE)
        if (source.all.isEmpty()) return
        val editor = to.getSharedPreferences(name, Context.MODE_PRIVATE).edit().clear()
        for ((key, value) in source.all) {
            when (value) {
                is String -> editor.putString(key, value)
                is Boolean -> editor.putBoolean(key, value)
                is Int -> editor.putInt(key, value)
                is Long -> editor.putLong(key, value)
                is Float -> editor.putFloat(key, value)
                is Set<*> -> {
                    @Suppress("UNCHECKED_CAST")
                    editor.putStringSet(key, value as Set<String>)
                }
            }
        }
        editor.commit()
    }

    private fun copyFilesDirectory(from: Context, to: Context, relativePath: String) {
        val sourceDir = File(from.filesDir, relativePath)
        if (!sourceDir.exists()) return
        val targetDir = File(to.filesDir, relativePath)
        sourceDir.walkTopDown().forEach { file ->
            val relative = file.relativeTo(sourceDir)
            val destination = File(targetDir, relative.path)
            if (file.isDirectory) {
                destination.mkdirs()
            } else {
                destination.parentFile?.mkdirs()
                file.copyTo(destination, overwrite = true)
            }
        }
    }

    private fun currentUserId(context: Context): Int = Process.myUid() / 100_000
}
