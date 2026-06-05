package com.timekpr.agent.service

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class AgentConnectionStatus {
    DISCONNECTED,
    CONNECTING,
    PENDING_APPROVAL,
    AUTHENTICATED,
    ERROR,
}

object AgentConnectionState {
    private val _status = MutableStateFlow(AgentConnectionStatus.DISCONNECTED)
    val status: StateFlow<AgentConnectionStatus> = _status.asStateFlow()

    private val _lastMessage = MutableStateFlow("")
    val lastMessage: StateFlow<String> = _lastMessage.asStateFlow()

    fun update(status: AgentConnectionStatus, message: String = "") {
        _status.value = status
        _lastMessage.value = message
    }
}
