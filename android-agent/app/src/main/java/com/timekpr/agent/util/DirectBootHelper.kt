package com.timekpr.agent.util

import android.content.Context
import android.os.Build
import android.os.UserManager

object DirectBootHelper {
    fun deviceProtectedContext(context: Context): Context {
        val appContext = context.applicationContext
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            appContext.createDeviceProtectedStorageContext()
        } else {
            appContext
        }
    }

    fun isCredentialStorageUnlocked(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return true
        val userManager = context.applicationContext.getSystemService(UserManager::class.java)
            ?: return true
        return userManager.isUserUnlocked
    }
}
