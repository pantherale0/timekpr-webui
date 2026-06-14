package com.guardian.agent.push

import android.content.Context
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.tasks.await

object PushTokenProvider {
    suspend fun getToken(context: Context): String? {
        return try {
            FirebaseMessaging.getInstance().token.await()
        } catch (_: Exception) {
            null
        }
    }

    fun registerTokenRefreshListener(context: Context, onToken: (String) -> Unit) {
        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            if (task.isSuccessful) {
                task.result?.let(onToken)
            }
        }
    }
}
