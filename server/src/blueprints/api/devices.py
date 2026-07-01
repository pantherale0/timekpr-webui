import json
import logging
import secrets
from flask import Blueprint, request, session, jsonify
from src.i18n.catalog import api_message
from src.models import db, AgentDevice
from src.agent.helper import AgentConnectionManager
from src.agent.push import notify_pairing_approved
from src.device.lifecycle import unenroll_device as lifecycle_unenroll_device
from src.common.helpers import _device_display_label, _build_device_label_map

_LOGGER = logging.getLogger(__name__)

api_devices_bp = Blueprint('api_devices', __name__)


@api_devices_bp.route('/api/device/approve/<system_id>', methods=['POST'])
def approve_device(system_id):
    """Approve a pending device and send its pairing token if connected."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
    
    device = AgentDevice.query.get(system_id)
    if not device:
        return jsonify({'success': False, 'message': api_message('device_not_found')}), 404
        
    if device.status != 'pending':
        return jsonify({
            'success': False,
            'message': api_message('device_not_pending', status=device.status),
        }), 400
        
    secure_token = secrets.token_hex(32)
    device.secure_token = secure_token
    device.status = 'approved'

    payload = request.get_json(silent=True) or {}
    display_name = (payload.get('display_name') or '').strip()
    if display_name:
        device.system_hostname = display_name[:255]

    db.session.commit()
    device_label = _device_display_label(system_id)
    
    delivered, delivery_message = notify_pairing_approved(system_id, secure_token)
    if not delivered:
        _LOGGER.warning(
            "pairing_approved not delivered immediately to %s: %s",
            system_id,
            delivery_message,
        )

    _LOGGER.info("Approved device %s and generated secure token.", system_id)
    return jsonify({
        'success': True,
        'message': api_message('device_approved', device=device_label),
    })


@api_devices_bp.route('/api/device/<system_id>/label', methods=['PATCH'])
def update_device_label(system_id):
    """Set a parent-friendly display label for an approved device."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    device = AgentDevice.query.get(system_id)
    if not device:
        return jsonify({'success': False, 'message': api_message('device_not_found')}), 404
    if device.status != 'approved':
        return jsonify({
            'success': False,
            'message': api_message('device_not_approved', status=device.status),
        }), 400

    payload = request.get_json(silent=True) or {}
    display_name = (payload.get('display_name') or '').strip()
    if not display_name:
        return jsonify({'success': False, 'message': api_message('device_label_required')}), 400

    device.system_hostname = display_name[:255]
    db.session.commit()
    device_label = _device_display_label(system_id)
    return jsonify({
        'success': True,
        'message': api_message('device_label_updated', device=device_label),
        'display_name': device_label,
    })


@api_devices_bp.route('/api/device/reject/<system_id>', methods=['POST'])
def reject_device(system_id):
    """Reject a device and close any pending or active websocket sessions."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
        
    device = AgentDevice.query.get(system_id)
    if not device:
        return jsonify({'success': False, 'message': api_message('device_not_found')}), 404
        
    device.status = 'rejected'
    device.secure_token = None
    db.session.commit()
    device_label = _device_display_label(system_id)
    
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
        
    _LOGGER.info("Rejected device %s and closed connections.", system_id)
    return jsonify({
        'success': True,
        'message': api_message('device_rejected', device=device_label),
    })


@api_devices_bp.route('/api/device/<system_id>/unenroll', methods=['POST'])
def unenroll_device(system_id):
    """Unenroll a device or request an Android factory reset."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    payload = request.get_json(silent=True) or {}
    mode = payload.get('mode', 'unenroll')
    result = lifecycle_unenroll_device(system_id, mode)
    status_code = result.pop('status_code', 200)
    return jsonify(result), status_code


@api_devices_bp.route('/api/devices/pending', methods=['GET'])
def get_pending_devices():
    """Return a JSON list of all pending devices for the onboarding wizard."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
    
    from src.common.helpers import filter_pending_devices_for_parent, resolve_session_parent_id

    parent_id = resolve_session_parent_id()
    pending = filter_pending_devices_for_parent(parent_id)
    label_map = _build_device_label_map(pending)
    results = []
    for device in pending:
        results.append({
            'system_id': device.system_id,
            'display_name': label_map.get(device.system_id, device.display_name),
            'system_ip': device.system_ip,
            'linux_users': device.linux_users,
            'platform': device.platform or 'linux'
        })
    return jsonify({'success': True, 'devices': results})

