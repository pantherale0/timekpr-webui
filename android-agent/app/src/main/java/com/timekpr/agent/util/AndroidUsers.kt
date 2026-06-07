package com.timekpr.agent.util

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.os.Build
import android.os.UserHandle
import android.os.UserManager
import android.provider.Settings
import android.util.Log
import com.timekpr.agent.admin.TimeKprDeviceAdminReceiver

object AndroidUsers {
    private const val TAG = "AndroidUsers"

    fun currentLinuxUsername(context: Context): String {
        return try {
            val userManager = context.getSystemService(UserManager::class.java)
            userManager?.userName?.takeIf { it.isNotBlank() } ?: "android"
        } catch (_: Exception) {
            "android"
        }
    }

    fun currentLinuxUid(context: Context): Int {
        return try {
            UserHandle.getUserHandleForUid(android.os.Process.myUid()).hashCode()
        } catch (_: Exception) {
            android.os.Process.myUid()
        }
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

        return users
    }

    fun activeUserUid(context: Context): Int {
        return try {
            val activityManager = context.getSystemService(android.app.ActivityManager::class.java)
            val getCurrentUserMethod = android.app.ActivityManager::class.java.getMethod("getCurrentUser")
            getCurrentUserMethod.invoke(activityManager) as? Int ?: 0
        } catch (_: Exception) {
            0
        }
    }

    fun getUserContext(context: Context, userId: Int): Context? {
        if (userId == 0) return context
        return try {
            val constructor = UserHandle::class.java.getConstructor(Int::class.javaPrimitiveType)
            val userHandle = constructor.newInstance(userId)
            val method = Context::class.java.getMethod(
                "createPackageContextAsUser",
                String::class.java,
                Int::class.javaPrimitiveType,
                UserHandle::class.java
            )
            method.invoke(context, context.packageName, 0, userHandle) as? Context
        } catch (e: Exception) {
            Log.e(TAG, "Failed to create context for user $userId via reflection", e)
            null
        }
    }
}
