"""REST API for desktop device screenshot history."""

import logging

from flask import Blueprint, jsonify, request, session, send_file
from io import BytesIO

from src.models import AgentDevice
from src.device.screenshot_settings import (
    build_settings_summary,
    get_or_create_settings,
    sync_screenshot_policy_for_device,
    upsert_settings,
)
from src.device.screenshots import (
    delete_all_screenshots_for_device,
    get_screenshot_by_id,
    list_screenshots_for_device,
)
from src.agent.helper import AgentConnectionManager

_LOGGER = logging.getLogger(__name__)

api_screenshots_bp = Blueprint('api_screenshots', __name__)


def _require_auth():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    return None


def _get_desktop_device_or_404(system_id):
    device = AgentDevice.query.get(system_id)
    if device is None:
        return None, (jsonify({'success': False, 'message': 'Device not found'}), 404)
    platform = (device.platform or 'linux').strip().lower()
    if platform in {'android', 'nintendo', 'xbox'}:
        return None, (
            jsonify({'success': False, 'message': 'Screen history is only available for Linux and Windows devices'}),
            400,
        )
    return device, None


@api_screenshots_bp.route('/api/devices/<system_id>/screenshot-settings', methods=['GET'])
def get_device_screenshot_settings(system_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    device, error_response = _get_desktop_device_or_404(system_id)
    if error_response is not None:
        return error_response

    try:
        settings = get_or_create_settings(device)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    return jsonify({
        'success': True,
        'settings': build_settings_summary(settings, device),
        'agent_online': AgentConnectionManager.is_online(system_id),
    })


@api_screenshots_bp.route('/api/devices/<system_id>/screenshot-settings', methods=['PUT'])
def update_device_screenshot_settings(system_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    device, error_response = _get_desktop_device_or_404(system_id)
    if error_response is not None:
        return error_response

    body = request.get_json(silent=True) or {}
    try:
        settings = upsert_settings(device, body)
        from src.models import db
        db.session.commit()
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    sync_success = False
    sync_message = None
    queued = False
    sync_success, sync_message = sync_screenshot_policy_for_device(device)
    if sync_message and 'Queued' in sync_message:
        queued = True

    status_code = 202 if queued else 200
    return jsonify({
        'success': True,
        'settings': build_settings_summary(settings, device),
        'sync_success': sync_success,
        'sync_message': sync_message,
        'queued': queued,
    }), status_code


@api_screenshots_bp.route('/api/devices/<system_id>/screenshot-settings/sync', methods=['POST'])
def sync_device_screenshot_settings(system_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    device, error_response = _get_desktop_device_or_404(system_id)
    if error_response is not None:
        return error_response

    success, message = sync_screenshot_policy_for_device(device)
    settings = get_or_create_settings(device)
    queued = bool(message and 'Queued' in message)
    status_code = 202 if queued else (200 if success else 409)
    return jsonify({
        'success': success,
        'message': message,
        'settings': build_settings_summary(settings, device),
        'queued': queued,
    }), status_code


@api_screenshots_bp.route('/api/devices/<system_id>/screenshots', methods=['GET'])
def list_device_screenshots(system_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    device, error_response = _get_desktop_device_or_404(system_id)
    if error_response is not None:
        return error_response

    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 24))
    linux_username = (request.args.get('linux_username') or '').strip() or None
    result = list_screenshots_for_device(
        system_id,
        page=page,
        per_page=per_page,
        linux_username=linux_username,
    )
    return jsonify({'success': True, **result})


@api_screenshots_bp.route('/api/screenshots/<int:screenshot_id>', methods=['GET'])
def get_screenshot_image(screenshot_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    screenshot = get_screenshot_by_id(screenshot_id)
    if screenshot is None:
        return jsonify({'success': False, 'message': 'Screenshot not found'}), 404

    return send_file(
        BytesIO(screenshot.data),
        mimetype=screenshot.mime_type,
        download_name=f'screenshot-{screenshot.screenshot_id}.jpg',
    )


@api_screenshots_bp.route('/api/devices/<system_id>/screenshots/capture', methods=['POST'])
def capture_device_screenshot(system_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    device, error_response = _get_desktop_device_or_404(system_id)
    if error_response is not None:
        return error_response

    body = request.get_json(silent=True) or {}
    linux_username = (body.get('linux_username') or '').strip() or None

    from src.agent.helper import AgentClient

    try:
        result = AgentClient(system_id).capture_screenshot(linux_username)
    except RuntimeError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 409

    queued = bool(isinstance(result, dict) and result.get('queued'))
    status_code = 202 if queued else 200
    return jsonify({'success': True, 'result': result, 'queued': queued}), status_code


@api_screenshots_bp.route('/api/devices/<system_id>/screenshots', methods=['DELETE'])
def clear_device_screenshots(system_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    device, error_response = _get_desktop_device_or_404(system_id)
    if error_response is not None:
        return error_response

    deleted = delete_all_screenshots_for_device(system_id)
    return jsonify({'success': True, 'deleted': deleted})
