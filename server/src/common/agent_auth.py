"""Shared bearer-token authentication for agent-facing HTTP APIs."""

from flask import jsonify, request

from src.models import AgentDevice, ManagedUserDeviceMap


def authenticate_agent_mapping(linux_username: str, *, require_username: bool = True):
    """Validate Authorization bearer token and optional linux_username mapping.

    Returns (device, mapping, error_response) where error_response is a Flask
  response tuple when authentication fails.
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None, None, (jsonify({
            'success': False,
            'message': 'Missing or invalid authorization header',
        }), 401)

    token = auth_header.split(' ', 1)[1].strip()
    if not token:
        return None, None, (jsonify({
            'success': False,
            'message': 'Missing or invalid authorization header',
        }), 401)

    device = AgentDevice.query.filter_by(secure_token=token, status='approved').first()
    if not device:
        return None, None, (jsonify({'success': False, 'message': 'Invalid token'}), 401)

    if not require_username:
        return device, None, None

    username = (linux_username or '').strip()
    if not username:
        return device, None, (jsonify({
            'success': False,
            'message': 'linux_username is required',
        }), 400)

    mapping = ManagedUserDeviceMap.query.filter_by(
        system_id=device.system_id,
        linux_username=username,
    ).first()
    if not mapping:
        return device, None, (jsonify({
            'success': False,
            'message': f'No user mapping for user {username} on this device',
        }), 400)

    return device, mapping, None
