"""Unified push/websocket delivery to agents."""

from __future__ import annotations

import json
import logging

from src.agent.helper import AgentConnectionManager
from src.models import AgentDevice
from src.common.fcm import (
    FCM_ACTION_COMMAND_WAKE,
    FCM_ACTION_CONNECT,
    FCM_ACTION_FACTORY_RESET,
    FCM_ACTION_PAIRING_APPROVED,
    FCM_ACTION_SYNC_POLICIES,
    is_fcm_configured,
    notify_android_agent,
)

_LOGGER = logging.getLogger(__name__)

PLATFORM_ANDROID = 'android'


def _is_android_device(device: AgentDevice | None) -> bool:
    if not device:
        return False
    platform = (device.platform or '').strip().lower()
    return platform == PLATFORM_ANDROID


def device_prefers_push(device: AgentDevice | None) -> bool:
    return _is_android_device(device) and bool((device.fcm_token or '').strip())


def android_push_wake_available(device: AgentDevice | None) -> bool:
    """True when the server can wake this Android device via FCM while offline."""
    if not _is_android_device(device):
        return False
    if not is_fcm_configured():
        return False
    return bool((device.fcm_token or '').strip())


def android_should_use_persistent_websocket(device: AgentDevice | None) -> bool:
    """Android agents should stay connected when FCM wake is unavailable."""
    return _is_android_device(device) and not android_push_wake_available(device)


def update_device_push_metadata(device: AgentDevice, hello_msg: dict) -> None:
    platform = hello_msg.get('platform')
    if isinstance(platform, str) and platform.strip():
        device.platform = platform.strip().lower()

    fcm_token = hello_msg.get('fcm_token')
    if isinstance(fcm_token, str) and fcm_token.strip():
        device.fcm_token = fcm_token.strip()
        from datetime import datetime, timezone

        device.fcm_token_updated_at = datetime.now(timezone.utc)

    is_device_owner = hello_msg.get('is_device_owner')
    if is_device_owner is not None:
        device.is_device_owner = bool(is_device_owner)


def notify_device_message(system_id: str, payload: dict) -> tuple[bool, str]:
    """
    Deliver a JSON payload to an agent.
    Uses the active WebSocket when online; otherwise FCM wake for Android devices.
    """
    device = AgentDevice.query.get(system_id)
    msg_type = payload.get('type')

    if AgentConnectionManager.is_online(system_id):
        return AgentConnectionManager.send_message(system_id, payload)

    if not device_prefers_push(device):
        return False, f"Agent for system ID '{system_id}' is offline"

    if msg_type == 'policy_sync_hint':
        return notify_android_agent(
            device,
            FCM_ACTION_SYNC_POLICIES,
            reason=payload.get('reason'),
        )

    if msg_type == 'pairing_approved':
        return notify_android_agent(
            device,
            FCM_ACTION_PAIRING_APPROVED,
            secure_token=payload.get('token'),
        )

    if msg_type == 'command_request' or msg_type == 'command_wake':
        return notify_android_agent(
            device,
            FCM_ACTION_COMMAND_WAKE,
            reason=payload.get('action') or msg_type,
        )

    return notify_android_agent(device, FCM_ACTION_CONNECT, reason=msg_type or 'server_message')


def wake_android_for_command(system_id: str, action: str | None = None) -> tuple[bool, str]:
    device = AgentDevice.query.get(system_id)
    if not device_prefers_push(device):
        return False, 'Device is not push-capable'
    return notify_android_agent(device, FCM_ACTION_COMMAND_WAKE, reason=action or 'command')


def wake_android_for_factory_reset(system_id: str) -> tuple[bool, str]:
    device = AgentDevice.query.get(system_id)
    if not device_prefers_push(device):
        return False, 'Device is not push-capable'
    return notify_android_agent(device, FCM_ACTION_FACTORY_RESET, reason='factory_reset')


def notify_policy_sync_hint(system_id: str, reason: str = 'server_update') -> tuple[bool, str]:
    return notify_device_message(
        system_id,
        {'type': 'policy_sync_hint', 'reason': reason},
    )


def notify_pairing_approved(system_id: str, secure_token: str) -> tuple[bool, str]:
    ws = AgentConnectionManager.get_pending_connection(system_id)
    if ws:
        try:
            ws.send(json.dumps({'type': 'pairing_approved', 'token': secure_token}))
            AgentConnectionManager.unregister_pending(system_id)
            return True, 'pairing_approved delivered over WebSocket'
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            _LOGGER.warning('WebSocket pairing_approved failed for %s: %s', system_id, exc)

    device = AgentDevice.query.get(system_id)
    if device_prefers_push(device):
        return notify_android_agent(
            device,
            FCM_ACTION_PAIRING_APPROVED,
            secure_token=secure_token,
        )

    return False, 'Device is not connected and has no FCM token'


def fcm_status_summary() -> dict:
    return {
        'configured': is_fcm_configured(),
    }
