"""REST API for AMAPI-aligned Android device restriction policies."""

import logging

from flask import Blueprint, jsonify, request, session

from src.android_device_policy_manager import (
    build_policy_summary,
    get_or_create_policy,
    upsert_policy,
)
from src.database import AgentDevice

_LOGGER = logging.getLogger(__name__)

api_android_device_policy_bp = Blueprint('api_android_device_policy', __name__)


def _require_auth():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    return None


def _get_device_or_404(system_id):
    device = AgentDevice.query.get(system_id)
    if device is None:
        return None, (jsonify({'success': False, 'message': 'Device not found'}), 404)
    return device, None


@api_android_device_policy_bp.route('/api/devices/<system_id>/android-device-policy', methods=['GET'])
def get_android_device_policy(system_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    device, error_response = _get_device_or_404(system_id)
    if error_response is not None:
        return error_response

    try:
        policy = get_or_create_policy(device)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    return jsonify({
        'success': True,
        'policy': build_policy_summary(policy, device),
    })


@api_android_device_policy_bp.route('/api/devices/<system_id>/android-device-policy', methods=['PUT'])
def update_android_device_policy(system_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    device, error_response = _get_device_or_404(system_id)
    if error_response is not None:
        return error_response

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({'success': False, 'message': 'Request body must be a JSON object'}), 400

    try:
        policy = upsert_policy(device, body)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    summary = build_policy_summary(policy, device)
    message = 'Device policy saved'
    if not policy.is_synced:
        message = f'Device policy saved; sync pending ({policy.last_sync_error or "agent offline"})'

    return jsonify({
        'success': True,
        'message': message,
        'policy': summary,
    })


@api_android_device_policy_bp.route('/api/mappings/<int:mapping_id>/android-device-policy', methods=['GET'])
def get_android_device_policy_legacy(mapping_id):
    from src.database import ManagedUserDeviceMap
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response
    mapping = ManagedUserDeviceMap.query.get(mapping_id)
    if mapping is None or not mapping.device:
        return jsonify({'success': False, 'message': 'Mapping or device not found'}), 404
    return get_android_device_policy(mapping.system_id)


@api_android_device_policy_bp.route('/api/mappings/<int:mapping_id>/android-device-policy', methods=['PUT'])
def update_android_device_policy_legacy(mapping_id):
    from src.database import ManagedUserDeviceMap
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response
    mapping = ManagedUserDeviceMap.query.get(mapping_id)
    if mapping is None or not mapping.device:
        return jsonify({'success': False, 'message': 'Mapping or device not found'}), 404
    return update_android_device_policy(mapping.system_id)
