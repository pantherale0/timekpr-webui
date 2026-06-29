package com.guardian.agent.service

import com.guardian.agent.protocol.AgentWebSocketClient
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AgentConnectionGateTest {
    @Test
    fun shortSessionSkipsWhenAnotherSessionIsActive() = runBlocking {
        val firstStarted = CompletableDeferred<Unit>()
        val releaseFirst = CompletableDeferred<Unit>()

        val firstJob = launch {
            AgentConnectionGate.run(AgentWebSocketClient.SessionMode.PAIRING_ONLY) {
                firstStarted.complete(Unit)
                releaseFirst.await()
                "first"
            }
        }
        firstStarted.await()

        val skipped = AgentConnectionGate.run(AgentWebSocketClient.SessionMode.SYNC) {
            "second"
        }
        assertNull(skipped)

        releaseFirst.complete(Unit)
        firstJob.join()
    }

    @Test
    fun persistentSessionWaitsForShortSessionToFinish() = runBlocking {
        val shortStarted = CompletableDeferred<Unit>()
        val releaseShort = CompletableDeferred<Unit>()
        val persistentResult = CompletableDeferred<String?>()

        val shortJob = launch {
            AgentConnectionGate.run(AgentWebSocketClient.SessionMode.SYNC) {
                shortStarted.complete(Unit)
                releaseShort.await()
                "short"
            }
        }
        shortStarted.await()

        val persistentJob = launch {
            persistentResult.complete(
                AgentConnectionGate.run(AgentWebSocketClient.SessionMode.PERSISTENT) {
                    "persistent"
                },
            )
        }

        releaseShort.complete(Unit)
        shortJob.join()
        assertEquals("persistent", persistentResult.await())
        persistentJob.join()
    }
}
