package com.guardian.agent.service

import com.guardian.agent.protocol.AgentWebSocketClient
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Ensures only one agent WebSocket session is open at a time.
 *
 * Short-lived sync/pairing callers skip when a session is already active.
 * The persistent foreground service waits until any in-flight session finishes.
 */
object AgentConnectionGate {
    private val sessionMutex = Mutex()

    @Volatile
    var activeMode: AgentWebSocketClient.SessionMode? = null
        private set

    suspend fun <T> run(
        mode: AgentWebSocketClient.SessionMode,
        block: suspend () -> T,
    ): T? {
        if (mode == AgentWebSocketClient.SessionMode.PERSISTENT) {
            return sessionMutex.withLock {
                activeMode = mode
                try {
                    block()
                } finally {
                    activeMode = null
                }
            }
        }

        if (!sessionMutex.tryLock()) {
            return null
        }
        return try {
            activeMode = mode
            block()
        } finally {
            activeMode = null
            sessionMutex.unlock()
        }
    }
}
