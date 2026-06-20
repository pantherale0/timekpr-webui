import logging

from flask import Blueprint, jsonify, request, session

from src.database import AgentDevice
from src.i18n.catalog import api_message
from src.windows_laps_manager import (
    clear_safe_mode_lockdown,
    get_windows_laps_status,
    reveal_escrowed_password,
)

_LOGGER = logging.getLogger(__name__)

api_windows_laps_bp = Blueprint('api_windows_laps', __name__)


def _require_admin_session():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
    return None


@api_windows_laps_bp.route('/api/devices/<system_id>/windows-laps', methods=['GET'])
def windows_laps_status(system_id):
    denied = _require_admin_session()
    if denied:
        return denied
    device = AgentDevice.query.get(system_id)
    if not device:
        return jsonify({'success': False, 'message': api_message('device_not_found')}), 404
    status_payload = get_windows_laps_status(device, reveal_password=False)
    return jsonify({'success': True, 'status': status_payload})


@api_windows_laps_bp.route('/api/devices/<system_id>/windows-laps/reveal-password', methods=['POST'])
def windows_laps_reveal_password(system_id):
    denied = _require_admin_session()
    if denied:
        return denied
    result = reveal_escrowed_password(system_id)
    status_code = result.pop('status_code', 200)
    return jsonify(result), status_code


@api_windows_laps_bp.route('/api/devices/<system_id>/windows-laps/clear-safe-mode-lockdown', methods=['POST'])
def windows_laps_clear_safe_mode_lockdown(system_id):
    denied = _require_admin_session()
    if denied:
        return denied
    result = clear_safe_mode_lockdown(system_id)
    status_code = result.pop('status_code', 200)
    return jsonify(result), status_code
