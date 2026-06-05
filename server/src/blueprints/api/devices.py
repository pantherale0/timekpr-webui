import json
import logging
import secrets
from flask import Blueprint, session, jsonify
from src.database import db, AgentDevice
from src.agent_helper import AgentConnectionManager
from src.agent_push import notify_pairing_approved
from src.helpers import _device_display_label

_LOGGER = logging.getLogger(__name__)

api_devices_bp = Blueprint('api_devices', __name__)


@api_devices_bp.route('/api/device/approve/<system_id>', methods=['POST'])
def approve_device(system_id):
    """Approve a pending device and send its pairing token if connected."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    device = AgentDevice.query.get(system_id)
    if not device:
        return jsonify({'success': False, 'message': 'Device not found'}), 404
        
    if device.status != 'pending':
        return jsonify({'success': False, 'message': f'Device is not pending (status: {device.status})'}), 400
        
    secure_token = secrets.token_hex(32)
    device.secure_token = secure_token
    device.status = 'approved'
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
    return jsonify({'success': True, 'message': f'Device {device_label} approved successfully.'})


@api_devices_bp.route('/api/device/reject/<system_id>', methods=['POST'])
def reject_device(system_id):
    """Reject a device and close any pending or active websocket sessions."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
        
    device = AgentDevice.query.get(system_id)
    if not device:
        return jsonify({'success': False, 'message': 'Device not found'}), 404
        
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
    return jsonify({'success': True, 'message': f'Device {device_label} rejected successfully.'})


@api_devices_bp.route('/api/devices/pending', methods=['GET'])
def get_pending_devices():
    """Return a JSON list of all pending devices for the onboarding wizard."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    pending = AgentDevice.query.filter_by(status='pending').all()
    results = []
    for device in pending:
        results.append({
            'system_id': device.system_id,
            'display_name': device.display_name,
            'system_ip': device.system_ip,
            'linux_users': device.linux_users,
            'platform': device.platform or 'linux'
        })
    return jsonify({'success': True, 'devices': results})

