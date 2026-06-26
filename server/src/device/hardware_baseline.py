"""Hardware BIOS baseline apply/audit orchestration and compliance persistence."""

import json
import logging
from datetime import datetime, timezone

from src.agent.helper import AgentClient, AgentConnectionManager
from src.models import AgentDevice, db
from src.common.settings import decrypt_setting, encrypt_setting

_LOGGER = logging.getLogger(__name__)

HARDWARE_BASELINE_PLATFORMS = {'linux', 'windows'}
VALID_COMPLIANCE_STATUSES = {
    'compliant',
    'non_compliant',
    'unknown',
    'unsupported',
    'pending',
}


def _parse_receipt_json(raw_value):
    if not raw_value:
        return {}
    try:
        payload = json.loads(raw_value)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _compliance_status_from_receipt(receipt):
    if not isinstance(receipt, dict):
        return 'unknown'
    overall = (receipt.get('overall') or '').strip().lower()
    if overall in VALID_COMPLIANCE_STATUSES:
        return overall
    return 'unknown'


def _primary_username(device):
    mapping = device.user_mappings[0] if device.user_mappings else None
    if mapping and mapping.linux_username:
        return mapping.linux_username
    users = device.linux_users or []
    if users:
        first = users[0]
        if isinstance(first, dict):
            return first.get('username') or ''
    return ''


def device_supports_hardware_baseline(device):
    platform = (device.platform or 'linux').strip().lower()
    return platform in HARDWARE_BASELINE_PLATFORMS


def get_hardware_baseline_status(device, reveal_password=False):
    receipt = _parse_receipt_json(device.hardware_compliance_json)
    status_payload = {
        'hardware_oem': device.hardware_oem,
        'hardware_oem_model': device.hardware_oem_model,
        'hardware_compliance_status': device.hardware_compliance_status or 'unknown',
        'hardware_compliance_checked_at': (
            device.hardware_compliance_checked_at.isoformat()
            if device.hardware_compliance_checked_at
            else None
        ),
        'receipt': receipt,
        'supported': device_supports_hardware_baseline(device),
        'agent_online': AgentConnectionManager.is_online(device.system_id),
        'has_escrowed_password': bool(device.bios_supervisor_password_escrow),
    }
    if reveal_password and device.bios_supervisor_password_escrow:
        status_payload['escrow_password'] = decrypt_setting(device.bios_supervisor_password_escrow)
    return status_payload


def _persist_hardware_result(device, receipt, escrow_password=None):
    now = datetime.now(timezone.utc)
    device.hardware_compliance_checked_at = now
    if isinstance(receipt, dict):
        device.hardware_compliance_json = json.dumps(receipt)
        device.hardware_compliance_status = _compliance_status_from_receipt(receipt)
        device.hardware_oem = receipt.get('oem') or device.hardware_oem
        model = receipt.get('model')
        if isinstance(model, str) and model.strip():
            device.hardware_oem_model = model.strip()[:128]
    if escrow_password:
        device.bios_supervisor_password_escrow = encrypt_setting(escrow_password)
    db.session.commit()


def apply_hardware_baseline(system_id, force_reset_password=False):
    device = AgentDevice.query.get(system_id)
    if not device:
        return {'success': False, 'message': 'Device not found', 'status_code': 404}
    if device.status != 'approved':
        return {
            'success': False,
            'message': f'Device is not approved (status: {device.status})',
            'status_code': 400,
        }
    if not device_supports_hardware_baseline(device):
        return {
            'success': False,
            'message': 'Hardware baseline is not supported on this platform',
            'status_code': 400,
        }
    if not AgentConnectionManager.is_online(system_id):
        return {'success': False, 'message': 'Agent is offline', 'status_code': 409}

    username = _primary_username(device)
    client = AgentClient(system_id)
    success, message, data = client.apply_hardware_baseline(
        username,
        force_reset_password=force_reset_password,
    )
    if not success:
        return {'success': False, 'message': message or 'Agent rejected hardware baseline apply', 'status_code': 502}

    receipt = data.get('receipt') if isinstance(data, dict) else None
    escrow_password = data.get('escrow_password') if isinstance(data, dict) else None
    if not isinstance(receipt, dict):
        return {'success': False, 'message': 'Agent returned an invalid compliance receipt', 'status_code': 502}

    _persist_hardware_result(device, receipt, escrow_password=escrow_password)
    return {
        'success': True,
        'message': message or 'Hardware baseline applied',
        'status': get_hardware_baseline_status(device),
        'status_code': 200,
    }


def audit_hardware_baseline(system_id):
    device = AgentDevice.query.get(system_id)
    if not device:
        return {'success': False, 'message': 'Device not found', 'status_code': 404}
    if device.status != 'approved':
        return {
            'success': False,
            'message': f'Device is not approved (status: {device.status})',
            'status_code': 400,
        }
    if not device_supports_hardware_baseline(device):
        return {
            'success': False,
            'message': 'Hardware baseline is not supported on this platform',
            'status_code': 400,
        }
    if not AgentConnectionManager.is_online(system_id):
        return {'success': False, 'message': 'Agent is offline', 'status_code': 409}

    username = _primary_username(device)
    client = AgentClient(system_id)
    success, message, data = client.audit_hardware_baseline(username)
    if not success:
        return {'success': False, 'message': message or 'Agent rejected hardware baseline audit', 'status_code': 502}

    receipt = data.get('receipt') if isinstance(data, dict) else None
    if not isinstance(receipt, dict):
        return {'success': False, 'message': 'Agent returned an invalid compliance receipt', 'status_code': 502}

    _persist_hardware_result(device, receipt)
    return {
        'success': True,
        'message': message or 'Hardware baseline audited',
        'status': get_hardware_baseline_status(device),
        'status_code': 200,
    }


def reveal_escrowed_password(system_id):
    device = AgentDevice.query.get(system_id)
    if not device:
        return {'success': False, 'message': 'Device not found', 'status_code': 404}
    if not device.bios_supervisor_password_escrow:
        return {'success': False, 'message': 'No escrowed supervisor password for this device', 'status_code': 404}

    password = decrypt_setting(device.bios_supervisor_password_escrow)
    _LOGGER.info('Hardware baseline supervisor password revealed for device %s', system_id)
    return {
        'success': True,
        'message': 'Supervisor password revealed',
        'escrow_password': password,
        'status_code': 200,
    }
