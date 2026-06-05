package com.timekpr.agent.monitor

import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArrayList

data class PendingAlert(
    val eventType: String,
    val linuxUsername: String,
    val details: JSONObject,
)

object AlertEventBus {
    private val listeners = CopyOnWriteArrayList<(PendingAlert) -> Unit>()
    private val queue = mutableListOf<PendingAlert>()

    fun emit(eventType: String, linuxUsername: String, details: JSONObject) {
        val alert = PendingAlert(eventType, linuxUsername, details)
        synchronized(queue) {
            queue.add(alert)
        }
        listeners.forEach { it(alert) }
    }

    fun drain(): List<PendingAlert> {
        synchronized(queue) {
            val copy = queue.toList()
            queue.clear()
            return copy
        }
    }

    fun setListener(listener: ((PendingAlert) -> Unit)?) {
        listeners.clear()
        if (listener != null) {
            listeners.add(listener)
        }
    }
}
