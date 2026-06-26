"""REST API for Linux device restriction policies."""

import logging

from flask import Blueprint, jsonify, request, session

from src.models import ManagedUserDeviceMap
from src.policy.linux import (
    build_policy_summary,
    get_or_create_policy,
    upsert_policy,
)

_LOGGER = logging.getLogger(__name__)

api_linux_device_policy_bp = Blueprint('api_linux_device_policy', __name__)


def _require_auth():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    return None


def _get_mapping_or_404(mapping_id):
    mapping = ManagedUserDeviceMap.query.get(mapping_id)
    if mapping is None:
        return None, (jsonify({'success': False, 'message': 'Mapping not found'}), 404)
    return mapping, None


@api_linux_device_policy_bp.route('/api/mappings/<int:mapping_id>/linux-device-policy', methods=['GET'])
def get_linux_device_policy(mapping_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    mapping, error_response = _get_mapping_or_404(mapping_id)
    if error_response is not None:
        return error_response

    try:
        policy = get_or_create_policy(mapping)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    return jsonify({
        'success': True,
        'policy': build_policy_summary(policy, mapping),
    })


@api_linux_device_policy_bp.route('/api/mappings/<int:mapping_id>/linux-device-policy', methods=['PUT'])
def update_linux_device_policy(mapping_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    mapping, error_response = _get_mapping_or_404(mapping_id)
    if error_response is not None:
        return error_response

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({'success': False, 'message': 'Request body must be a JSON object'}), 400

    try:
        policy = upsert_policy(mapping, body)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    summary = build_policy_summary(policy, mapping)
    message = 'Device policy saved'
    if not policy.is_synced:
        message = f'Device policy saved; sync pending ({policy.last_sync_error or "agent offline"})'

    return jsonify({
        'success': True,
        'message': message,
        'policy': summary,
    })
