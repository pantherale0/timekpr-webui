package com.guardian.agent.util

import android.accounts.AccountManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.Settings

object GoogleAccountSetupHelper {
    const val GOOGLE_ACCOUNT_TYPE = "com.google"
    private const val GMS_PACKAGE = "com.google.android.gms"

    fun isGmsAvailable(context: Context): Boolean {
        return try {
            context.packageManager.getPackageInfo(GMS_PACKAGE, 0)
            true
        } catch (_: PackageManager.NameNotFoundException) {
            false
        }
    }

    fun canAddGoogleAccount(context: Context): Boolean {
        if (!isGmsAvailable(context)) {
            return false
        }
        val intent = buildAddAccountIntent()
        return intent.resolveActivity(context.packageManager) != null
    }

    fun hasGoogleAccount(context: Context): Boolean {
        return try {
            AccountManager.get(context).getAccountsByType(GOOGLE_ACCOUNT_TYPE).isNotEmpty()
        } catch (_: SecurityException) {
            false
        }
    }

    fun primaryGoogleAccountName(context: Context): String? {
        return try {
            AccountManager.get(context)
                .getAccountsByType(GOOGLE_ACCOUNT_TYPE)
                .firstOrNull()
                ?.name
        } catch (_: SecurityException) {
            null
        }
    }

    fun buildAddAccountIntent(): Intent {
        return Intent(Settings.ACTION_ADD_ACCOUNT).apply {
            putExtra(Settings.EXTRA_ACCOUNT_TYPES, arrayOf(GOOGLE_ACCOUNT_TYPE))
        }
    }
}
