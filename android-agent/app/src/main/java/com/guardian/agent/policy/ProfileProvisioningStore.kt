package com.guardian.agent.policy

import android.content.Context
import org.json.JSONObject

/**
 * Maps server-side linux usernames to Android user IDs for programmatic profile creation.
 *
 * Android often reports secondary users as "User 10" rather than the name passed to
 * [android.app.admin.DevicePolicyManager.createAndManageUser], so we persist the mapping
 * locally and use it for idempotent provisioning and hello inventory reporting.
 */
class ProfileProvisioningStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun record(username: String, userId: Int) {
        val key = normalize(username)
        if (key.isEmpty() || userId < 0) return
        val root = loadRoot()
        root.put(key, userId)
        root.put(displayKey(key), username.trim())
        persist(root)
    }

    fun userIdFor(username: String): Int? {
        val stored = loadRoot().optInt(normalize(username), -1)
        return stored.takeIf { it >= 0 }
    }

    fun displayNameForUserId(userId: Int): String? {
        val root = loadRoot()
        val keys = root.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            if (key.startsWith(DISPLAY_PREFIX)) continue
            if (root.optInt(key, -1) == userId) {
                return root.optString(displayKey(key)).takeIf { it.isNotBlank() }
            }
        }
        return null
    }

    fun allProvisionedUserIds(): Set<Int> {
        val root = loadRoot()
        val ids = mutableSetOf<Int>()
        val keys = root.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            if (key.startsWith(DISPLAY_PREFIX)) continue
            root.optInt(key, -1).takeIf { it >= 0 }?.let { ids.add(it) }
        }
        return ids
    }

    fun isProvisioned(username: String, activeUserIds: Set<Int>): Boolean {
        val userId = userIdFor(username) ?: return false
        return userId in activeUserIds
    }

    fun prune(activeUserIds: Set<Int>) {
        val root = loadRoot()
        val toRemove = mutableListOf<String>()
        val keys = root.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            if (key.startsWith(DISPLAY_PREFIX)) continue
            if (root.optInt(key, -1) !in activeUserIds) {
                toRemove += key
                toRemove += displayKey(key)
            }
        }
        if (toRemove.isEmpty()) return
        toRemove.forEach { root.remove(it) }
        persist(root)
    }

    private fun loadRoot(): JSONObject {
        val raw = prefs.getString(KEY_MAPPINGS, null) ?: return JSONObject()
        return try {
            JSONObject(raw)
        } catch (_: Exception) {
            JSONObject()
        }
    }

    private fun persist(root: JSONObject) {
        prefs.edit().putString(KEY_MAPPINGS, root.toString()).apply()
    }

    private fun normalize(username: String): String = username.trim().lowercase()

    private fun displayKey(normalizedUsername: String): String = "$DISPLAY_PREFIX$normalizedUsername"

    companion object {
        private const val PREFS_NAME = "guardian_profile_provisioning"
        private const val KEY_MAPPINGS = "username_to_user_id"
        private const val DISPLAY_PREFIX = "display:"
    }
}
