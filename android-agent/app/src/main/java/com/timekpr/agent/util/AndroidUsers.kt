package com.timekpr.agent.util

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.os.Build
import android.os.Process
import android.os.UserHandle
import android.os.UserManager
import android.provider.Settings
import android.util.Log
import com.timekpr.agent.admin.TimeKprDeviceAdminReceiver
import com.timekpr.agent.policy.ProfileProvisioningStore

object AndroidUsers {
    private const val TAG = "AndroidUsers"

    fun displayNameForUser(context: Context, userId: Int): String? {
        ProfileProvisioningStore(context).displayNameForUserId(userId)?.let { return it }
        return try {
            val userManager = context.getSystemService(UserManager::class.java) ?: return null
            val getUserInfoMethod = UserManager::class.java.getMethod(
                "getUserInfo",
                Int::class.javaPrimitiveType,
            )
            val userInfo = getUserInfoMethod.invoke(userManager, userId) ?: return null
            userInfo.javaClass.getField("name").get(userInfo) as? String
        } catch (_: Exception) {
            null
        }
    }

    fun currentLinuxUsername(context: Context): String {
        val userId = Process.myUid() / 100_000
        displayNameForUser(context, userId)?.let { return it }
        return try {
            val userManager = context.getSystemService(UserManager::class.java)
            userManager?.userName?.takeIf { it.isNotBlank() && !it.equals("android", ignoreCase = true) }
                ?: "User $userId"
        } catch (_: Exception) {
            "User $userId"
        }
    }

    fun currentLinuxUid(context: Context): Int = Process.myUid() / 100_000

    /**
     * Resolve the Android multi-user ID for [username] using provisioning registry,
     * hello inventory, or an optional server hint. Avoids defaulting to user 0 when
     * validating managed secondary profiles from the primary user's WebSocket session.
     */
    fun resolveUidForUsername(
        context: Context,
        username: String,
        hintedUid: Int? = null,
    ): Int {
        val normalized = username.trim()
        if (normalized.isEmpty()) {
            return hintedUid?.takeIf { it >= 0 } ?: currentLinuxUid(context)
        }

        ProfileProvisioningStore(context).userIdFor(normalized)?.let { userId ->
            if (userId >= 0) return userId
        }

        for (entry in linuxUsersPayload(context)) {
            val reported = entry["username"] as? String ?: continue
            if (reported.equals(normalized, ignoreCase = true)) {
                val uid = (entry["uid"] as? Number)?.toInt()
                if (uid != null && uid >= 0) return uid
            }
        }

        if (hintedUid != null && hintedUid > 0) return hintedUid

        if (normalized.equals(currentLinuxUsername(context), ignoreCase = true)) {
            return currentLinuxUid(context)
        }

        return hintedUid ?: currentLinuxUid(context)
    }

    fun deviceHostname(context: Context): String {
        return Settings.Global.getString(context.contentResolver, Settings.Global.DEVICE_NAME)
            ?.takeIf { it.isNotBlank() }
            ?: Build.MODEL
    }

    fun linuxUsersPayload(context: Context): List<Map<String, Any>> {
        val userManager = context.getSystemService(UserManager::class.java) ?: return emptyList()
        val users = mutableListOf<Map<String, Any>>()

        // 1. Try reflection on UserManager.getUsers()
        try {
            val getUsersMethod = UserManager::class.java.getMethod("getUsers")
            val rawUsers = getUsersMethod.invoke(userManager) as? List<*>
            if (rawUsers != null) {
                for (userInfo in rawUsers) {
                    if (userInfo != null) {
                        val id = userInfo.javaClass.getField("id").get(userInfo) as? Int ?: continue
                        val name = userInfo.javaClass.getField("name").get(userInfo) as? String ?: "User $id"
                        
                        users.add(
                            mapOf(
                                "username" to name,
                                "uid" to id,
                                "platform" to "android"
                            )
                        )
                    }
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Reflection to list users failed, falling back to public APIs", e)
        }

        // 2. If reflection failed or returned empty, use public APIs as fallback
        if (users.isEmpty()) {
            val userHandles = mutableSetOf<UserHandle>()
            
            // Add user profiles of the calling user
            try {
                userHandles.addAll(userManager.userProfiles)
            } catch (e: Exception) {
                Log.e(TAG, "Failed to get user profiles", e)
            }

            // If API 28+ and app is device owner/admin, we can get secondary users
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                val dpm = context.getSystemService(DevicePolicyManager::class.java)
                val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
                if (dpm != null && dpm.isAdminActive(admin)) {
                    try {
                        val secondary = dpm.getSecondaryUsers(admin)
                        if (secondary != null) {
                            userHandles.addAll(secondary)
                        }
                    } catch (e: Exception) {
                        Log.e(TAG, "Failed to get secondary users", e)
                    }
                }
            }

            for (handle in userHandles) {
                val id = handle.hashCode() // Fallback key, but getIdentifier() is cleaner
                val actualId = try {
                    val getIdentifierMethod = UserHandle::class.java.getMethod("getIdentifier")
                    getIdentifierMethod.invoke(handle) as? Int ?: id
                } catch (e: Exception) {
                    id
                }
                
                val name = if (actualId == 0) {
                    "System User"
                } else {
                    // Try to get username via reflection if possible
                    try {
                        val getUserInfoMethod = UserManager::class.java.getMethod("getUserInfo", Int::class.javaPrimitiveType)
                        val userInfo = getUserInfoMethod.invoke(userManager, actualId)
                        if (userInfo != null) {
                            userInfo.javaClass.getField("name").get(userInfo) as? String
                        } else {
                            null
                        }
                    } catch (e: Exception) {
                        null
                    } ?: "User $actualId"
                }

                users.add(
                    mapOf(
                        "username" to name,
                        "uid" to actualId,
                        "platform" to "android"
                    )
                )
            }
        }

        // If all fallback attempts failed, report the current user
        if (users.isEmpty()) {
            val currentUsername = currentLinuxUsername(context)
            val currentUid = currentLinuxUid(context)
            users.add(
                mapOf(
                    "username" to currentUsername,
                    "uid" to currentUid,
                    "platform" to "android"
                )
            )
        }

        return applyProvisionedDisplayNames(context, users)
    }

    private fun applyProvisionedDisplayNames(
        context: Context,
        users: List<Map<String, Any>>,
    ): List<Map<String, Any>> {
        val store = ProfileProvisioningStore(context)
        return users.map { user ->
            val uid = (user["uid"] as? Number)?.toInt() ?: return@map user
            val displayName = store.displayNameForUserId(uid) ?: return@map user
            user.toMutableMap().apply { put("username", displayName) }
        }
    }

    fun activeUserUid(context: Context): Int {
        val callingUserId = Process.myUid() / 100_000
        if (callingUserId == 0) {
            return try {
                val activityManager = context.getSystemService(android.app.ActivityManager::class.java)
                val getCurrentUserMethod = android.app.ActivityManager::class.java.getMethod("getCurrentUser")
                getCurrentUserMethod.invoke(activityManager) as? Int ?: callingUserId
            } catch (_: Exception) {
                callingUserId
            }
        }
        return callingUserId
    }

    fun getUserContext(context: Context, userId: Int): Context? {
        val currentUserId = Process.myUid() / 100_000
        if (userId == currentUserId) return context
        return try {
            val constructor = UserHandle::class.java.getConstructor(Int::class.javaPrimitiveType)
            val userHandle = constructor.newInstance(userId)
            val method = Context::class.java.getMethod(
                "createPackageContextAsUser",
                String::class.java,
                Int::class.javaPrimitiveType,
                UserHandle::class.java,
            )
            method.invoke(context, context.packageName, 0, userHandle) as? Context
        } catch (e: Exception) {
            Log.e(TAG, "Failed to create context for user $userId", e)
            null
        }
    }
}
