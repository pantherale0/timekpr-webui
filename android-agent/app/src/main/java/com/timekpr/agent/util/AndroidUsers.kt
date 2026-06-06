package com.timekpr.agent.util

import android.content.Context
import android.os.Build
import android.os.UserManager
import android.provider.Settings

object AndroidUsers {
    @Suppress("MissingPermission")
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
            android.os.UserHandle.getUserHandleForUid(android.os.Process.myUid()).hashCode()
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
        val username = currentLinuxUsername(context)
        val uid = currentLinuxUid(context)
        return listOf(
            mapOf(
                "username" to username,
                "uid" to uid,
                "platform" to "android",
            ),
        )
    }
}
