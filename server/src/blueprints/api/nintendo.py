import aiohttp
import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, session
from src.models import db, AgentDevice, Settings
from src.common.nintendo_sync import run_async
from pynintendoparental import Authenticator, NintendoParental

_LOGGER = logging.getLogger(__name__)
api_nintendo_bp = Blueprint('api_nintendo', __name__)

NINTENDO_SESSION_TOKEN_KEY = 'nintendo_session_token'
NINTENDO_LINKED_AT_KEY = 'nintendo_linked_at'


def get_nintendo_account_summary(*, validate=False):
    """Return linked-account metadata for settings UI and wizard checks."""
    session_token = Settings.get_value(NINTENDO_SESSION_TOKEN_KEY)
    linked_at = Settings.get_value(NINTENDO_LINKED_AT_KEY)
    enrolled_devices = AgentDevice.query.filter_by(platform='nintendo', status='approved').order_by(
        AgentDevice.date_added.desc()
    ).all()

    summary = {
        'linked': bool(session_token),
        'linked_at': linked_at,
        'enrolled_device_count': len(enrolled_devices),
        'enrolled_devices': [
            {
                'system_id': device.system_id,
                'display_name': device.display_name,
            }
            for device in enrolled_devices
        ],
        'token_valid': None,
    }

    if validate and session_token:
        try:
            run_async(_list_devices_async(session_token))
            summary['token_valid'] = True
        except Exception as exc:
            _LOGGER.warning("Nintendo token validation failed: %s", exc)
            summary['token_valid'] = False

    return summary


def _enrolled_nintendo_device_ids():
    return {
        device.system_id
        for device in AgentDevice.query.filter_by(platform='nintendo').all()
    }

async def _get_login_url_async():
    async with aiohttp.ClientSession() as client_session:
        auth = Authenticator(client_session=client_session)
        return auth.login_url, auth._auth_code_verifier

@api_nintendo_bp.route('/api/nintendo/login-url', methods=['GET'])
def get_login_url():
    """Generate the OAuth login URL and cache the code verifier in the session."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    try:
        login_url, code_verifier = run_async(_get_login_url_async())
        session['nintendo_code_verifier'] = code_verifier
        return jsonify({
            'success': True,
            'login_url': login_url
        })
    except Exception as exc:
        _LOGGER.error("Failed to generate login URL: %s", exc)
        return jsonify({'success': False, 'message': f'Failed to generate login URL: {str(exc)}'}), 500

async def _authenticate_async(response_url, code_verifier):
    async with aiohttp.ClientSession() as client_session:
        auth = Authenticator(client_session=client_session)
        # Restore the verifier to match the initial challenge generated
        auth._auth_code_verifier = code_verifier
        await auth.async_complete_login(response_url)
        return auth.session_token

@api_nintendo_bp.route('/api/nintendo/authenticate', methods=['POST'])
def authenticate_nintendo():
    """Complete the authentication process using the pasted redirect URL."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    payload = request.get_json() or {}
    response_url = payload.get('response_url')
    code_verifier = session.get('nintendo_code_verifier')

    if not response_url or not code_verifier:
        return jsonify({'success': False, 'message': 'Missing response URL or session state'}), 400

    try:
        session_token = run_async(_authenticate_async(response_url, code_verifier))
        if session_token:
            Settings.set_value(NINTENDO_SESSION_TOKEN_KEY, session_token)
            Settings.set_value(NINTENDO_LINKED_AT_KEY, datetime.now(timezone.utc).isoformat())
            session.pop('nintendo_code_verifier', None)
            return jsonify({'success': True, 'message': 'Successfully linked Nintendo Account!'})
        return jsonify({'success': False, 'message': 'Failed to obtain session token'}), 400
    except Exception as exc:
        _LOGGER.error("Nintendo login error: %s", exc)
        return jsonify({'success': False, 'message': f'Authentication failed: {str(exc)}'}), 500

async def _list_devices_async(session_token):
    async with aiohttp.ClientSession() as client_session:
        auth = Authenticator(session_token, client_session)
        await auth.async_complete_login(use_session_token=True)
        client = await NintendoParental.create(auth)
        enrolled_ids = _enrolled_nintendo_device_ids()
        devices = []
        for device in client.devices.values():
            players = []
            for player in device.players.values():
                players.append({
                    'player_id': player.player_id,
                    'nickname': player.nickname,
                    'player_image': player.player_image,
                    'playing_time': player.playing_time
                })
            devices.append({
                'device_id': device.device_id,
                'name': device.name,
                'model': device.model,
                'limit_time': device.limit_time,
                'today_playing_time': device.today_playing_time,
                'enrolled': device.device_id in enrolled_ids,
                'players': players
            })
        return devices

@api_nintendo_bp.route('/api/nintendo/devices', methods=['GET'])
def list_nintendo_devices():
    """List all consoles associated with the linked Nintendo account."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    session_token = Settings.get_value(NINTENDO_SESSION_TOKEN_KEY)
    if not session_token:
        return jsonify({'success': False, 'message': 'Nintendo Account is not linked'}), 400

    try:
        devices = run_async(_list_devices_async(session_token))
        return jsonify({'success': True, 'devices': devices})
    except Exception as exc:
        _LOGGER.error("Nintendo list devices error: %s", exc)
        return jsonify({'success': False, 'message': str(exc)}), 500


@api_nintendo_bp.route('/api/nintendo/account-status', methods=['GET'])
def nintendo_account_status():
    """Return whether a Nintendo account is linked and basic enrollment info."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    validate = request.args.get('validate', '').strip().lower() in {'1', 'true', 'yes'}
    summary = get_nintendo_account_summary(validate=validate)
    return jsonify({'success': True, **summary})


@api_nintendo_bp.route('/api/nintendo/unlink', methods=['POST'])
def unlink_nintendo_account():
    """Remove the stored Nintendo account session."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    Settings.set_value(NINTENDO_SESSION_TOKEN_KEY, '')
    Settings.set_value(NINTENDO_LINKED_AT_KEY, '')
    session.pop('nintendo_code_verifier', None)
    return jsonify({'success': True, 'message': 'Nintendo Account unlinked.'})

@api_nintendo_bp.route('/api/nintendo/import-device', methods=['POST'])
def import_device():
    """Enroll a chosen Switch console as an AgentDevice."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    payload = request.get_json() or {}
    device_id = payload.get('device_id')
    display_name = payload.get('name') or "Nintendo Switch"

    if not device_id:
        return jsonify({'success': False, 'message': 'Device ID is required'}), 400

    from src.common.helpers import resolve_session_parent_id, resolve_active_household_for_write

    parent_id = resolve_session_parent_id()
    household_id = resolve_active_household_for_write(parent_id) if parent_id else None

    existing = AgentDevice.query.get(device_id)
    if existing:
        return jsonify({'success': False, 'message': 'Device is already enrolled'}), 400

    device = AgentDevice(
        system_id=device_id,
        system_hostname=display_name,
        platform='nintendo',
        status='approved',
        household_id=household_id,
    )
    db.session.add(device)
    db.session.commit()

    try:
        from app import task_manager
        task_manager.sync_nintendo_devices(force=True)
    except Exception as exc:
        _LOGGER.warning("Initial Nintendo sync after import failed: %s", exc)

    return jsonify({'success': True, 'message': f'Console {display_name} enrolled successfully!'})


@api_nintendo_bp.route('/api/nintendo/sync', methods=['POST'])
def sync_nintendo_now():
    """Force an immediate Nintendo cloud sync for enrolled consoles."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    session_token = Settings.get_value(NINTENDO_SESSION_TOKEN_KEY)
    if not session_token:
        return jsonify({'success': False, 'message': 'Nintendo Account is not linked'}), 400

    try:
        Settings.set_value('nintendo_sync_requested', '1')
        return jsonify({'success': True, 'message': 'Nintendo cloud sync request queued.'}), 202
    except Exception as exc:
        _LOGGER.error("Manual Nintendo sync queue failed: %s", exc)
        return jsonify({'success': False, 'message': str(exc)}), 500
