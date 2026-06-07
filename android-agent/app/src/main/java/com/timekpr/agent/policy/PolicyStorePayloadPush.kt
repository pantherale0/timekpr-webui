package com.timekpr.agent.policy

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.UserHandle
import com.timekpr.agent.util.AgentLog
import com.timekpr.agent.admin.CrossUserStoreSync
import java.io.File

/** Pushes replicated policy XML to a managed secondary user via explicit broadcast. */
object PolicyStorePayloadPush {
    private val handler = Handler(Looper.getMainLooper())
    private val pendingUserIds = mutableSetOf<Int>()
    private var scheduledContext: Context? = null

    /** Coalesce rapid pushes during sync into one broadcast per user. */
    fun pushToUser(primaryContext: Context, targetUserId: Int) {
        if (targetUserId == 0) return
        scheduledContext = primaryContext.applicationContext
        pendingUserIds.add(targetUserId)
        handler.removeCallbacks(flushRunnable)
        handler.postDelayed(flushRunnable, DEBOUNCE_MS)
    }

    private val flushRunnable = Runnable {
        val ctx = scheduledContext ?: return@Runnable
        val userIds = pendingUserIds.toList()
        pendingUserIds.clear()
        for (userId in userIds) {
            pushToUserImmediate(ctx, userId)
        }
    }

    private fun pushToUserImmediate(primaryContext: Context, targetUserId: Int) {
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
            AgentLog.d(TAG, "Pushed $payloadCount policy store payload(s) to user $targetUserId")
        } catch (e: Exception) {
            AgentLog.wOnce(TAG, "push_$targetUserId", "Failed to push policy store payloads to user $targetUserId")
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
    private const val DEBOUNCE_MS = 400L
}
