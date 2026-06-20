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


def _queue_lifecycle_command(system_id: str, action: str, username: str) -> tuple[bool, str]:
    from src.pending_commands_manager import enqueue_command

    try:
        enqueue_command(system_id, action, username=username or None, args={})
        return True, f'Device offline; {action} queued for next connection'
    except ValueError as exc:
        return False, str(exc)


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
    queued_for_reconnect = False
    delivery_message = ''
    keep_token_for_queue = False

    if normalized_mode == MODE_FACTORY_RESET:
        if AgentConnectionManager.is_online(system_id) or device_prefers_push(device):
            success, message = client.factory_reset_device(username)
            delivery_message = message or ''
            delivered_to_agent = success
            if not success:
                queued, queue_message = _queue_lifecycle_command(
                    system_id,
                    MODE_FACTORY_RESET,
                    username,
                )
                if queued:
                    queued_for_reconnect = True
                    keep_token_for_queue = True
                    delivery_message = queue_message
        else:
            queued, delivery_message = _queue_lifecycle_command(
                system_id,
                MODE_FACTORY_RESET,
                username,
            )
            queued_for_reconnect = queued
            keep_token_for_queue = queued
    else:
        if AgentConnectionManager.is_online(system_id) or device_prefers_push(device):
            success, message = client.unenroll_device(username)
            delivery_message = message or ''
            delivered_to_agent = success
            if not success and not AgentConnectionManager.is_online(system_id):
                queued, queue_message = _queue_lifecycle_command(
                    system_id,
                    MODE_UNENROLL,
                    username,
                )
                if queued:
                    queued_for_reconnect = True
                    delivery_message = queue_message
        else:
            queued, delivery_message = _queue_lifecycle_command(
                system_id,
                MODE_UNENROLL,
                username,
            )
            queued_for_reconnect = queued

    device.pending_factory_reset = False
    _revoke_server_trust(
        device,
        keep_token_for_pending_reset=keep_token_for_queue and bool(device.secure_token),
    )
    db.session.commit()

    _LOGGER.info(
        "Unenrolled device %s (mode=%s, delivered=%s, queued=%s)",
        system_id,
        normalized_mode,
        delivered_to_agent,
        queued_for_reconnect,
    )

    status_code = 202 if queued_for_reconnect else 200
    return {
        'success': True,
        'message': delivery_message or 'Device unenrolled successfully',
        'delivered_to_agent': delivered_to_agent,
        'factory_reset_requested': factory_reset_requested,
        'pending_factory_reset': queued_for_reconnect and factory_reset_requested,
        'queued': queued_for_reconnect,
        'server_revoked': True,
        'status_code': status_code,
    }


def deliver_pending_factory_reset_on_connect(system_id: str) -> bool:
    """
    Deliver a legacy pending_factory_reset flag or queued factory reset on connect.

    Returns True when a factory reset was dispatched.
    """
    from src.pending_commands_manager import (
        PendingCommand,
        enqueue_command,
        flush_pending_commands,
    )

    device = AgentDevice.query.get(system_id)
    if not device or not _is_android_device(device):
        return False

    username = _resolve_command_username(device)
    if device.pending_factory_reset:
        try:
            enqueue_command(system_id, 'factory_reset', username=username or None, args={})
        except ValueError as exc:
            _LOGGER.warning(
                'Failed to queue legacy factory reset for %s: %s',
                system_id,
                exc,
            )
        device.pending_factory_reset = False
        db.session.commit()

    pending_row = PendingCommand.query.filter_by(
        system_id=system_id,
        action=MODE_FACTORY_RESET,
        status=PendingCommand.STATUS_PENDING,
    ).first()
    if pending_row is None:
        return False

    result = flush_pending_commands(system_id)
    if result.delivered == 0:
        return False

    device = AgentDevice.query.get(system_id)
    if device is not None:
        device.secure_token = None
        db.session.commit()

    _LOGGER.info(
        "Delivered queued factory reset to %s (failed=%s)",
        system_id,
        result.failed,
    )
    _close_device_connections(system_id)
    return True
