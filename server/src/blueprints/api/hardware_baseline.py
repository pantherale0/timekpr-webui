import hashlib
import json
import logging
import os

from flask import Blueprint, current_app, jsonify, request, send_from_directory, session

from src.models import AgentDevice
from src.device.hardware_baseline import (
    apply_hardware_baseline,
    audit_hardware_baseline,
    get_hardware_baseline_status,
    reveal_escrowed_password,
)
from src.i18n.catalog import api_message

_LOGGER = logging.getLogger(__name__)

api_hardware_baseline_bp = Blueprint('api_hardware_baseline', __name__)

SUPPORTED_BIOS_PAYLOAD_VENDORS = {'dell', 'hp', 'lenovo', 'surface'}


def _require_admin_session():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
    return None


def _authenticate_agent_bearer():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None, (jsonify({'success': False, 'message': 'Missing or invalid authorization header'}), 401)
    token = auth_header.split(' ', 1)[1].strip()
    device = AgentDevice.query.filter_by(secure_token=token, status='approved').first()
    if not device:
        return None, (jsonify({'success': False, 'message': 'Invalid token'}), 401)
    return device, None


def _bios_payloads_dir():
    return os.path.join(current_app.static_folder, 'bios-payloads')


def _load_bios_payload_manifest():
    manifest_path = os.path.join(_bios_payloads_dir(), 'manifest.json')
    if not os.path.isfile(manifest_path):
        return {}
    try:
        with open(manifest_path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        _LOGGER.warning('Failed to read BIOS payload manifest: %s', exc)
        return {}
    return payload if isinstance(payload, dict) else {}


@api_hardware_baseline_bp.route('/api/devices/<system_id>/hardware-baseline/apply', methods=['POST'])
def hardware_baseline_apply(system_id):
    denied = _require_admin_session()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    force_reset_password = bool(payload.get('force_reset_password'))
    result = apply_hardware_baseline(system_id, force_reset_password=force_reset_password)
    status_code = result.pop('status_code', 200)
    return jsonify(result), status_code


@api_hardware_baseline_bp.route('/api/devices/<system_id>/hardware-baseline/audit', methods=['POST'])
def hardware_baseline_audit(system_id):
    denied = _require_admin_session()
    if denied:
        return denied
    result = audit_hardware_baseline(system_id)
    status_code = result.pop('status_code', 200)
    return jsonify(result), status_code


@api_hardware_baseline_bp.route('/api/devices/<system_id>/hardware-baseline/status', methods=['GET'])
def hardware_baseline_status(system_id):
    denied = _require_admin_session()
    if denied:
        return denied
    device = AgentDevice.query.get(system_id)
    if not device:
        return jsonify({'success': False, 'message': api_message('device_not_found')}), 404
    status_payload = get_hardware_baseline_status(device, reveal_password=False)
    return jsonify({'success': True, 'status': status_payload})


@api_hardware_baseline_bp.route('/api/devices/<system_id>/hardware-baseline/reveal-password', methods=['POST'])
def hardware_baseline_reveal_password(system_id):
    denied = _require_admin_session()
    if denied:
        return denied
    result = reveal_escrowed_password(system_id)
    status_code = result.pop('status_code', 200)
    return jsonify(result), status_code


@api_hardware_baseline_bp.route('/api/agent/bios-payloads/<vendor>', methods=['GET'])
def agent_bios_payload_download(vendor):
    device, error = _authenticate_agent_bearer()
    if error:
        return error

    vendor_key = (vendor or '').strip().lower()
    if vendor_key not in SUPPORTED_BIOS_PAYLOAD_VENDORS:
        return jsonify({'success': False, 'message': f'Unsupported vendor: {vendor}'}), 400

    if (device.platform or '').strip().lower() != 'windows':
        return jsonify({'success': False, 'message': 'BIOS payload download is only available on Windows agents'}), 400

    manifest = _load_bios_payload_manifest()
    vendor_manifest = manifest.get(vendor_key) if isinstance(manifest, dict) else None
    if not isinstance(vendor_manifest, dict):
        return jsonify({'success': False, 'message': f'No payload manifest entry for vendor {vendor_key}'}), 404

    archive_name = (vendor_manifest.get('archive') or '').strip()
    if not archive_name:
        return jsonify({'success': False, 'message': 'Payload manifest is missing archive name'}), 404

    archive_path = os.path.join(_bios_payloads_dir(), vendor_key, archive_name)
    if not os.path.isfile(archive_path):
        return jsonify({'success': False, 'message': 'Payload archive not found on server'}), 404

    digest = hashlib.sha256()
    with open(archive_path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)

    response = send_from_directory(
        os.path.join(_bios_payloads_dir(), vendor_key),
        archive_name,
        as_attachment=True,
    )
    response.headers['X-Guardian-Payload-Sha256'] = digest.hexdigest()
    if vendor_manifest.get('version'):
        response.headers['X-Guardian-Payload-Version'] = str(vendor_manifest['version'])
    return response
