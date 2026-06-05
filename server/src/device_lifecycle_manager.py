"""Device unenrollment and factory reset orchestration."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.agent_helper import AgentClient, AgentConnectionManager
from src.agent_push import device_prefers_push
from src.database import AgentDevice, db

_LOGGER = logging.getLogger(__name__)

MODE_UNENROLL = 'unenroll'
MODE_FACTORY_RESET = 'factory_reset'
VALID_MODES = {MODE_UNENROLL, MODE_FACTORY_RESET}


def _is_android_device(device: AgentDevice) -> bool:
    return (device.platform or '').strip().lower() == 'android'


def _close_device_connections(system_id: str) -> None:
    ws_pending = AgentConnectionManager.get_pending_connection(system_id)
    if ws_pending:
        try:
            ws_pending.close()
        except Exception:
            pass
        AgentConnectionManager.unregister_pending(system_id)

    ws_active = AgentConnectionManager.get_connection(system_id)
    if ws_active:
        try:
            ws_active.close()
        except Exception:
            pass
        AgentConnectionManager.unregister(system_id)


def _resolve_command_username(device: AgentDevice) -> str:
    if device.user_mappings:
        return device.user_mappings[0].linux_username or ''
    for entry in device.linux_users:
        if isinstance(entry, dict):
            username = entry.get('username') or entry.get('name')
            if username:
                return str(username)
        elif isinstance(entry, str) and entry.strip():
            return entry.strip()
    return ''


def _revoke_server_trust(device: AgentDevice, *, keep_token_for_pending_reset: bool = False) -> None:
    device.status = 'rejected'
    if not keep_token_for_pending_reset:
        device.secure_token = None
    device.unenrolled_at = datetime.now(timezone.utc)
    _close_device_connections(device.system_id)


def unenroll_device(system_id: str, mode: str) -> dict:
    """
    Unenroll or factory-reset a device.

    Returns a structured result with delivery flags for the admin UI.
    """
    normalized_mode = (mode or '').strip().lower()
    if normalized_mode not in VALID_MODES:
        return {
            'success': False,
            'message': f"Invalid mode '{mode}'. Use 'unenroll' or 'factory_reset'.",
            'status_code': 400,
        }

    device = AgentDevice.query.get(system_id)
    if not device:
        return {
            'success': False,
            'message': 'Device not found',
            'status_code': 404,
        }

    if device.status == 'rejected':
        return {
            'success': False,
            'message': 'Device is already unenrolled',
            'status_code': 400,
        }

    if normalized_mode == MODE_FACTORY_RESET and not _is_android_device(device):
        return {
            'success': False,
            'message': 'Factory reset is only supported for Android devices',
            'status_code': 400,
        }

    username = _resolve_command_username(device)
    client = AgentClient(system_id)
    delivered_to_agent = False
    factory_reset_requested = normalized_mode == MODE_FACTORY_RESET
    pending_factory_reset = False
    delivery_message = ''

    if normalized_mode == MODE_FACTORY_RESET:
        if AgentConnectionManager.is_online(system_id) or device_prefers_push(device):
            success, message = client.factory_reset_device(username)
            delivery_message = message or ''
            delivered_to_agent = success
            if not success and device_prefers_push(device):
                pending_factory_reset = True
        else:
            pending_factory_reset = True
            delivery_message = 'Device offline; factory reset queued for next connection'
    else:
        if AgentConnectionManager.is_online(system_id) or device_prefers_push(device):
            success, message = client.unenroll_device(username)
            delivery_message = message or ''
            delivered_to_agent = success
        else:
            delivery_message = 'Device offline; server trust revoked without agent cleanup'

    device.pending_factory_reset = pending_factory_reset
    _revoke_server_trust(
        device,
        keep_token_for_pending_reset=pending_factory_reset and bool(device.secure_token),
    )
    db.session.commit()

    _LOGGER.info(
        "Unenrolled device %s (mode=%s, delivered=%s, pending_reset=%s)",
        system_id,
        normalized_mode,
        delivered_to_agent,
        pending_factory_reset,
    )

    return {
        'success': True,
        'message': delivery_message or 'Device unenrolled successfully',
        'delivered_to_agent': delivered_to_agent,
        'factory_reset_requested': factory_reset_requested,
        'pending_factory_reset': pending_factory_reset,
        'server_revoked': True,
        'status_code': 200,
    }


def deliver_pending_factory_reset_on_connect(system_id: str) -> bool:
    """
    Send a queued factory reset immediately after WebSocket authentication.

    Returns True when a pending reset was dispatched.
    """
    device = AgentDevice.query.get(system_id)
    if not device or not device.pending_factory_reset or not _is_android_device(device):
        return False

    username = _resolve_command_username(device)
    client = AgentClient(system_id)
    success, message = client.factory_reset_device(username)
    device.pending_factory_reset = False
    if success:
        device.secure_token = None
    db.session.commit()

    _LOGGER.info(
        "Delivered pending factory reset to %s (success=%s, message=%s)",
        system_id,
        success,
        message,
    )

    _close_device_connections(system_id)
    return True
