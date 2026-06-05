import logging

from flask import Blueprint, jsonify, request, send_file, session
from io import BytesIO

from src.agent_helper import AgentConnectionManager
from src.pairing_helper import (
    build_agent_websocket_url,
    build_pairing_payload,
    pairing_payload_json,
    render_pairing_qr_png,
)

_LOGGER = logging.getLogger(__name__)

api_pairing_bp = Blueprint('api_pairing', __name__)


@api_pairing_bp.route('/api/pairing/config', methods=['GET'])
def pairing_config():
    """Return JSON payload used by Android/Linux agents during QR setup."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    explicit_url = (request.args.get('server_url') or '').strip() or None
    server_url = build_agent_websocket_url(request, explicit_url=explicit_url)
    registration_token = AgentConnectionManager.registration_token
    payload = build_pairing_payload(server_url, registration_token)
    return jsonify({'success': True, 'payload': payload})


@api_pairing_bp.route('/api/pairing/qr.png', methods=['GET'])
def pairing_qr_png():
    """Render a QR code PNG for the current server pairing payload."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    explicit_url = (request.args.get('server_url') or '').strip() or None
    server_url = build_agent_websocket_url(request, explicit_url=explicit_url)
    registration_token = AgentConnectionManager.registration_token
    payload_json = pairing_payload_json(server_url, registration_token)
    png_bytes = render_pairing_qr_png(payload_json)
    return send_file(
        BytesIO(png_bytes),
        mimetype='image/png',
        download_name='timekpr-pairing.png',
        max_age=0,
    )
