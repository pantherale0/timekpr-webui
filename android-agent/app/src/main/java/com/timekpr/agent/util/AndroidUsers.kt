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
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.admin.TimeKprDeviceAdminReceiver
import com.timekpr.agent.policy.ProfileProvisioningStore

object AndroidUsers {
    private const val TAG = "AndroidUsers"
    private const val CACHE_TTL_MS = 30_000L

    private var cachedUsersPayload: List<Map<String, Any>>? = null
    private var cacheUserId = -1
    private var cacheTimeMs = 0L
    private var loggedReflectionFallback = false

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

    fun isPrimaryUser(context: Context): Boolean = currentLinuxUid(context) == 0

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

        if (!isPrimaryUser(context)) {
            if (normalized.equals(currentLinuxUsername(context), ignoreCase = true)) {
                return currentLinuxUid(context)
            }
            return hintedUid?.takeIf { it >= 0 } ?: -1
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

        return hintedUid?.takeIf { it >= 0 } ?: -1
    }

    fun deviceHostname(context: Context): String {
        return Settings.Global.getString(context.contentResolver, Settings.Global.DEVICE_NAME)
            ?.takeIf { it.isNotBlank() }
            ?: Build.MODEL
    }

    fun linuxUsersPayload(context: Context): List<Map<String, Any>> {
        val callingUserId = currentLinuxUid(context)
        val now = System.currentTimeMillis()
        if (
            cachedUsersPayload != null &&
            cacheUserId == callingUserId &&
            now - cacheTimeMs < CACHE_TTL_MS
        ) {
            return cachedUsersPayload!!
        }

        val result = buildLinuxUsersPayload(context)
        cachedUsersPayload = result
        cacheUserId = callingUserId
        cacheTimeMs = now
        return result
    }

    /** Provisioned secondary profile IDs (excludes user 0). Primary user only. */
    fun managedSecondaryUserIds(context: Context): Set<Int> {
        if (!isPrimaryUser(context)) return emptySet()
        val fromProvisioning = ProfileProvisioningStore(context).allProvisionedUserIds().filter { it > 0 }
        if (fromProvisioning.isNotEmpty()) return fromProvisioning.toSet()
        return linuxUsersPayload(context)
            .mapNotNull { (it["uid"] as? Number)?.toInt() }
            .filter { it > 0 }
            .toSet()
    }

    private fun buildLinuxUsersPayload(context: Context): List<Map<String, Any>> {
        val callingUserId = currentLinuxUid(context)
        if (callingUserId != 0) {
            return applyProvisionedDisplayNames(
                context,
                listOf(
                    mapOf(
                        "username" to currentLinuxUsername(context),
                        "uid" to callingUserId,
                        "platform" to "android",
                    ),
                ),
            )
        }

        val userManager = context.getSystemService(UserManager::class.java) ?: return emptyList()
        val users = mutableListOf<Map<String, Any>>()

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
                                "platform" to "android",
                            ),
                        )
                    }
                }
            }
        } catch (_: Exception) {
            if (!loggedReflectionFallback) {
                loggedReflectionFallback = true
                Log.d(TAG, "UserManager.getUsers unavailable; using public user APIs")
            }
        }

        if (users.isEmpty()) {
            val userHandles = mutableSetOf<UserHandle>()
            try {
                userHandles.addAll(userManager.userProfiles)
            } catch (_: Exception) {
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P && DeviceOwnerProvisioner.isDeviceOwner(context)) {
                val dpm = context.getSystemService(DevicePolicyManager::class.java)
                val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
                if (dpm != null && dpm.isAdminActive(admin)) {
                    try {
                        dpm.getSecondaryUsers(admin)?.let { userHandles.addAll(it) }
                    } catch (_: SecurityException) {
                    }
                }
            }

            for (handle in userHandles) {
                val actualId = try {
                    val getIdentifierMethod = UserHandle::class.java.getMethod("getIdentifier")
                    getIdentifierMethod.invoke(handle) as? Int ?: handle.hashCode()
                } catch (_: Exception) {
                    handle.hashCode()
                }

                val name = if (actualId == 0) {
                    "System User"
                } else {
                    try {
                        val getUserInfoMethod = UserManager::class.java.getMethod(
                            "getUserInfo",
                            Int::class.javaPrimitiveType,
                        )
                        val userInfo = getUserInfoMethod.invoke(userManager, actualId)
                        if (userInfo != null) {
                            userInfo.javaClass.getField("name").get(userInfo) as? String
                        } else {
                            null
                        }
                    } catch (_: Exception) {
                        null
                    } ?: "User $actualId"
                }

                users.add(
                    mapOf(
                        "username" to name,
                        "uid" to actualId,
                        "platform" to "android",
                    ),
                )
            }
        }

        if (users.isEmpty()) {
            users.add(
                mapOf(
                    "username" to currentLinuxUsername(context),
                    "uid" to callingUserId,
                    "platform" to "android",
                ),
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
            Log.d(TAG, "Failed to create context for user $userId: ${e.message}")
            null
        }
    }

    fun usernameForUid(context: Context, uid: Int, timeLimitStore: com.timekpr.agent.policy.TimeLimitStore? = null): String {
        timeLimitStore?.getUsernameForUid(uid)?.let { return it }
        ProfileProvisioningStore(context).displayNameForUserId(uid)?.let { return it }
        displayNameForUser(context, uid)?.let { return it }
        for (entry in linuxUsersPayload(context)) {
            val entryUid = (entry["uid"] as? Number)?.toInt()
            if (entryUid == uid) {
                (entry["username"] as? String)?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
            }
        }
        if (uid == currentLinuxUid(context)) {
            return currentLinuxUsername(context)
        }
        return "User $uid"
    }

    /**
     * Device owner (user 0) reports every managed profile; secondary processes report only themselves.
     */
    fun inventoryTargets(context: Context): List<Pair<String, Context>> {
        if (!isPrimaryUser(context)) {
            return listOf(currentLinuxUsername(context) to context)
        }

        val results = mutableListOf<Pair<String, Context>>()
        for (entry in linuxUsersPayload(context)) {
            val uid = (entry["uid"] as? Number)?.toInt() ?: continue
            val username = (entry["username"] as? String)?.trim()?.takeIf { it.isNotEmpty() } ?: continue
            val userContext = if (uid == currentLinuxUid(context)) {
                context
            } else {
                getUserContext(context, uid) ?: continue
            }
            results.add(username to userContext)
        }
        if (results.isEmpty()) {
            results.add(currentLinuxUsername(context) to context)
        }
        return results
    }
}
