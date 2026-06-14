package com.guardian.agent.service

import android.content.Context
import android.content.Intent

/**
 * Legacy entry point — delegates to [AgentSessionCoordinator] (FCM + WorkManager)
 * instead of maintaining a persistent WebSocket.
 */
object AgentWebSocketService {
    fun start(context: Context) {
        AgentSessionCoordinator.startMobileAgent(context)
    }
}
