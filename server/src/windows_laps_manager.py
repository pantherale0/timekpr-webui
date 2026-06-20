"""Windows local Administrator password (LAPS-style) escrow orchestration."""

import logging
from datetime import datetime, timezone

from src.agent_helper import AgentClient, AgentConnectionManager
from src.database import AgentDevice, db
from src.settings_manager import decrypt_setting, encrypt_setting

_LOGGER = logging.getLogger(__name__)


def device_supports_windows_laps(device):
    return (device.platform or '').strip().lower() == 'windows'


def get_windows_laps_status(device, reveal_password=False):
    status_payload = {
        'supported': device_supports_windows_laps(device),
        'agent_online': AgentConnectionManager.is_online(device.system_id),
        'has_escrowed_password': bool(device.windows_local_admin_password_escrow),
        'rotated_at': (
            device.windows_local_admin_rotated_at.isoformat()
            if device.windows_local_admin_rotated_at
            else None
        ),
        'rotation_id': device.windows_local_admin_rotation_id,
    }
    if reveal_password and device.windows_local_admin_password_escrow:
        status_payload['escrow_password'] = decrypt_setting(device.windows_local_admin_password_escrow)
    return status_payload


def persist_credential_escrow(system_id, credential_type, rotation_id, password, occurred_at=None):
    if credential_type != 'windows_local_admin':
        raise ValueError(f'Unsupported credential_type: {credential_type}')

    device = AgentDevice.query.get(system_id)
    if not device:
        raise ValueError('Device not found')
    if device.status != 'approved':
        raise ValueError(f'Device is not approved (status: {device.status})')
    if not device_supports_windows_laps(device):
        raise ValueError('Windows LAPS is only supported on Windows agents')

    parsed_at = occurred_at or datetime.now(timezone.utc)
    if parsed_at.tzinfo is None:
        parsed_at = parsed_at.replace(tzinfo=timezone.utc)

    device.windows_local_admin_password_escrow = encrypt_setting(password)
    device.windows_local_admin_rotated_at = parsed_at
    device.windows_local_admin_rotation_id = (rotation_id or '')[:64] or None
    db.session.commit()
    _LOGGER.info(
        'Stored Windows local admin escrow for %s (rotation_id=%s)',
        system_id,
        device.windows_local_admin_rotation_id,
    )
    return device


def reveal_escrowed_password(system_id):
    device = AgentDevice.query.get(system_id)
    if not device:
        return {'success': False, 'message': 'Device not found', 'status_code': 404}
    if not device.windows_local_admin_password_escrow:
        return {'success': False, 'message': 'No escrowed local administrator password', 'status_code': 404}
    return {
        'success': True,
        'password': decrypt_setting(device.windows_local_admin_password_escrow),
        'rotated_at': (
            device.windows_local_admin_rotated_at.isoformat()
            if device.windows_local_admin_rotated_at
            else None
        ),
        'rotation_id': device.windows_local_admin_rotation_id,
    }


def clear_safe_mode_lockdown(system_id):
    device = AgentDevice.query.get(system_id)
    if not device:
        return {'success': False, 'message': 'Device not found', 'status_code': 404}
    if device.status != 'approved':
        return {
            'success': False,
            'message': f'Device is not approved (status: {device.status})',
            'status_code': 400,
        }
    if not device_supports_windows_laps(device):
        return {'success': False, 'message': 'Safe Mode lockdown override is Windows-only', 'status_code': 400}
    if not AgentConnectionManager.is_online(system_id):
        return {'success': False, 'message': 'Agent is offline', 'status_code': 409}

    client = AgentClient(system_id)
    success, message, _data = client.clear_safe_mode_lockdown()
    if not success:
        return {'success': False, 'message': message or 'Agent rejected override', 'status_code': 502}
    return {'success': True, 'message': message or 'Safe Mode lockdown override sent to agent'}
