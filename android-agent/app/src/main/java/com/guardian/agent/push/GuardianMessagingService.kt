package com.guardian.agent.push

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.guardian.agent.admin.DeviceLifecycleManager
import com.guardian.agent.service.AgentSessionCoordinator

/**
 * FCM data messages wake the agent for short WebSocket sync sessions instead of
 * keeping a socket open continuously.
 */
class GuardianMessagingService : FirebaseMessagingService() {
    override fun onMessageReceived(message: RemoteMessage) {
        val action = message.data["action"] ?: return
        when (action) {
            FcmActions.PAIRING_APPROVED -> {
                val token = message.data["token"]
                if (!token.isNullOrBlank()) {
                    AgentSessionCoordinator.handlePairingApproved(applicationContext, token)
                } else {
                    AgentSessionCoordinator.scheduleSync(applicationContext, reason = action)
                }
            }
            FcmActions.SYNC_POLICIES,
            FcmActions.CONNECT,
            FcmActions.COMMAND_WAKE -> {
                AgentSessionCoordinator.scheduleSync(applicationContext, reason = action)
            }
        }
    }

    override fun onNewToken(token: String) {
        AgentSessionCoordinator.scheduleTokenRegistration(applicationContext, token)
    }
}

object FcmActions {
    const val SYNC_POLICIES = "sync_policies"
    const val PAIRING_APPROVED = "pairing_approved"
    const val CONNECT = "connect"
    const val COMMAND_WAKE = "command_wake"
    const val FACTORY_RESET = "factory_reset"
}
