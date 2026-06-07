package com.timekpr.agent.policy

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.UserHandle
import android.util.Log
import com.timekpr.agent.admin.CrossUserStoreSync
import java.io.File

/** Pushes replicated policy XML to a managed secondary user via explicit broadcast. */
object PolicyStorePayloadPush {
    fun pushToUser(primaryContext: Context, targetUserId: Int) {
        if (targetUserId == 0) return
        val userHandle = userHandleForId(targetUserId) ?: return
        val intent = Intent(PolicyStoreReloadReceiver.ACTION_RELOAD_STORES)
            .setComponent(ComponentName(primaryContext.packageName, PolicyStoreReloadReceiver::class.java.name))
        var payloadCount = 0
        for (prefsName in CrossUserStoreSync.replicatedPrefNames()) {
            val sourceFile = File(primaryContext.applicationInfo.dataDir, "shared_prefs/$prefsName.xml")
            if (!sourceFile.exists()) continue
            intent.putExtra(extraKey(prefsName), sourceFile.readText())
            payloadCount++
        }
        if (payloadCount == 0) return
        try {
            primaryContext.sendBroadcastAsUser(intent, userHandle)
            Log.i(TAG, "Pushed $payloadCount policy store payload(s) to user $targetUserId")
        } catch (e: Exception) {
            Log.w(TAG, "Failed to push policy store payloads to user $targetUserId", e)
        }
    }

    fun extraKey(prefsName: String): String = "prefs_xml_$prefsName"

    private fun userHandleForId(userId: Int): UserHandle? {
        return try {
            val constructor = UserHandle::class.java.getConstructor(Int::class.javaPrimitiveType)
            constructor.newInstance(userId) as UserHandle
        } catch (_: Exception) {
            null
        }
    }

    private const val TAG = "PolicyStorePayloadPush"
}
