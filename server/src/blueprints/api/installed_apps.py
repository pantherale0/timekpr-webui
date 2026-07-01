import logging

from flask import Blueprint, Response, jsonify, request, session

from src.agent.helper import refresh_installed_apps as agent_refresh_installed_apps
from src.models import AgentDevice, ManagedUser
from src.device.installed_apps import (
    get_icon,
    list_installed_apps_for_device,
    list_installed_apps_for_managed_user,
)

_LOGGER = logging.getLogger(__name__)

api_installed_apps_bp = Blueprint('api_installed_apps', __name__)


def _require_auth():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    return None


@api_installed_apps_bp.route('/devices/<system_id>/installed-apps', methods=['GET'])
def get_device_installed_apps(system_id):
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    device = AgentDevice.query.get(system_id)
    if device is None:
        return jsonify({'success': False, 'message': 'Device not found'}), 404

    linux_username = request.args.get('linux_username')
    present_only = request.args.get('present_only', 'true').strip().lower() != 'false'

    apps = list_installed_apps_for_device(
        system_id,
        linux_username=linux_username,
        present_only=present_only,
    )
    return jsonify({
        'success': True,
        'system_id': system_id,
        'apps': [app.to_dict() for app in apps],
        'report_hash': device.installed_apps_report_hash,
        'last_reported': device.installed_apps_last_reported.isoformat()
        if device.installed_apps_last_reported else None,
        'count': device.installed_apps_count,
    })


@api_installed_apps_bp.route('/managed-users/<int:managed_user_id>/installed-apps', methods=['GET'])
def get_managed_user_installed_apps(managed_user_id):
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    from src.common.helpers import check_parent_child_access
    check_parent_child_access(managed_user_id)

    user = ManagedUser.query.get(managed_user_id)
    if user is None:
        return jsonify({'success': False, 'message': 'Managed user not found'}), 404

    present_only = request.args.get('present_only', 'true').strip().lower() != 'false'
    apps = list_installed_apps_for_managed_user(managed_user_id, present_only=present_only)
    return jsonify({
        'success': True,
        'managed_user_id': managed_user_id,
        'apps': apps,
    })


@api_installed_apps_bp.route('/apps/icons/<content_hash>', methods=['GET'])
def get_application_icon(content_hash):
    icon = get_icon(content_hash)
    if icon is None:
        return jsonify({'success': False, 'message': 'Icon not found'}), 404

    response = Response(icon.data, mimetype=icon.mime_type)
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response


@api_installed_apps_bp.route('/devices/<system_id>/installed-apps/refresh', methods=['POST'])
def refresh_device_installed_apps(system_id):
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    device = AgentDevice.query.get(system_id)
    if device is None:
        return jsonify({'success': False, 'message': 'Device not found'}), 404

    payload = request.get_json(silent=True) or {}
    linux_username = (payload.get('linux_username') or '').strip()
    if not linux_username:
        mapping = device.user_mappings[0] if device.user_mappings else None
        if mapping is None:
            return jsonify({
                'success': False,
                'message': 'linux_username is required when device has no mappings',
            }), 400
        linux_username = mapping.linux_username

    try:
        result = agent_refresh_installed_apps(system_id, linux_username)
    except RuntimeError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 503

    queued = bool(isinstance(result, dict) and result.get('queued'))
    status_code = 202 if queued else 200
    return jsonify({'success': True, 'result': result, 'queued': queued}), status_code
