import logging
import time

from flask import Blueprint, jsonify, session

from src.auth.session_lifecycle import extend_parent_session, get_oidc_expires_at
from src.i18n.catalog import api_message

_LOGGER = logging.getLogger(__name__)

api_session_bp = Blueprint('api_session', __name__)


@api_session_bp.route('/api/session/extend', methods=['POST'])
def extend_session():
    """Let the parent extend their sign-in before OIDC access tokens expire."""
    from app import oidc_helper

    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    success, error_key = extend_parent_session(session, oidc_helper)
    if not success:
        return jsonify({'success': False, 'message': api_message(error_key)}), 400

    expires_at = get_oidc_expires_at(session)
    payload = {
        'success': True,
        'message': api_message('session_extended'),
        'expires_at': int(expires_at) if expires_at is not None else None,
    }
    return jsonify(payload)


@api_session_bp.route('/api/session/status', methods=['GET'])
def session_status():
    """Return sign-in expiry metadata for the session warning banner."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    expires_at = get_oidc_expires_at(session)
    if expires_at is None:
        return jsonify({'success': True, 'expires_at': None})

    return jsonify({
        'success': True,
        'expires_at': int(expires_at),
        'server_time': int(time.time()),
    })
