import aiohttp
import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, session
from src.models import db, AgentDevice, Settings
from src.common.xbox_sync import run_async
from pyfamilysafety import Authenticator, FamilySafety

_LOGGER = logging.getLogger(__name__)
api_xbox_bp = Blueprint('api_xbox', __name__)

XBOX_REFRESH_TOKEN_KEY = 'xbox_refresh_token'
XBOX_LINKED_AT_KEY = 'xbox_linked_at'


def get_xbox_account_summary(*, validate=False):
    """Return linked-account metadata for settings UI and wizard checks."""
    session_token = Settings.get_value(XBOX_REFRESH_TOKEN_KEY)
    linked_at = Settings.get_value(XBOX_LINKED_AT_KEY)
    enrolled_devices = AgentDevice.query.filter_by(platform='xbox', status='approved').order_by(
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
            _LOGGER.warning("Xbox token validation failed: %s", exc)
            summary['token_valid'] = False

    return summary


def _enrolled_xbox_device_ids():
    return {
        device.system_id
        for device in AgentDevice.query.filter_by(platform='xbox').all()
    }


@api_xbox_bp.route('/api/xbox/login-url', methods=['GET'])
def get_login_url():
    """Generate the Microsoft OAuth login URL."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    # Microsoft Live SDK client ID & Family Safety scope
    client_id = "000000000004893A"
    redirect_uri = "https://login.live.com/oauth20_desktop.srf"
    scope = "service::familymobile.microsoft.com::MBI_SSL"
    login_url = (
        f"https://login.live.com/oauth20_authorize.srf"
        f"?cobrandid=b5d15d4b-695a-4cd5-93c6-13f551b310df"
        f"&client_id={client_id}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scope}"
    )

    return jsonify({
        'success': True,
        'login_url': login_url
    })


async def _authenticate_async(response_url):
    auth = await Authenticator.create(response_url, use_refresh_token=False)
    return auth.refresh_token


@api_xbox_bp.route('/api/xbox/authenticate', methods=['POST'])
def authenticate_xbox():
    """Complete the authentication process using the pasted redirect URL."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    payload = request.get_json() or {}
    response_url = payload.get('response_url')

    if not response_url:
        return jsonify({'success': False, 'message': 'Missing response URL'}), 400

    try:
        refresh_token = run_async(_authenticate_async(response_url))
        if refresh_token:
            Settings.set_value(XBOX_REFRESH_TOKEN_KEY, refresh_token)
            Settings.set_value(XBOX_LINKED_AT_KEY, datetime.now(timezone.utc).isoformat())
            return jsonify({'success': True, 'message': 'Successfully linked Xbox Account!'})
        return jsonify({'success': False, 'message': 'Failed to obtain refresh token'}), 400
    except Exception as exc:
        _LOGGER.error("Xbox login error: %s", exc)
        return jsonify({'success': False, 'message': f'Authentication failed: {str(exc)}'}), 500


async def _list_devices_async(session_token):
    auth = await Authenticator.create(session_token, use_refresh_token=True)
    client = FamilySafety(auth)
    await client.update()

    enrolled_ids = _enrolled_xbox_device_ids()
    devices = []
    seen_device_ids = set()

    # Roster list of family members for user mapping steps
    players = []
    for account in client.accounts:
        players.append({
            'player_id': account.user_id,
            'nickname': f"{account.first_name} {account.surname or ''}".strip(),
            'player_image': account.profile_picture,
            'playing_time': account.today_screentime_usage,
        })

    for account in client.accounts:
        if not account.devices:
            continue
        for device in account.devices:
            os_name = (device.os_name or '').strip().lower()
            device_class = (device.device_class or '').strip().lower()
            is_xbox = 'xbox' in os_name or 'xbox' in device_class
            if not is_xbox:
                continue

            if device.device_id in seen_device_ids:
                continue
            seen_device_ids.add(device.device_id)

            devices.append({
                'device_id': device.device_id,
                'name': device.device_name or "Xbox Console",
                'model': device.device_model or device.os_name or "Xbox",
                'today_playing_time': device.today_time_used,
                'enrolled': device.device_id in enrolled_ids,
                'players': players
            })

    return devices


@api_xbox_bp.route('/api/xbox/devices', methods=['GET'])
def list_xbox_devices():
    """List all consoles associated with the linked Microsoft account."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    session_token = Settings.get_value(XBOX_REFRESH_TOKEN_KEY)
    if not session_token:
        return jsonify({'success': False, 'message': 'Xbox Account is not linked'}), 400

    try:
        devices = run_async(_list_devices_async(session_token))
        return jsonify({'success': True, 'devices': devices})
    except Exception as exc:
        _LOGGER.error("Xbox list devices error: %s", exc)
        return jsonify({'success': False, 'message': str(exc)}), 500


@api_xbox_bp.route('/api/xbox/account-status', methods=['GET'])
def xbox_account_status():
    """Return whether an Xbox account is linked and basic enrollment info."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    validate = request.args.get('validate', '').strip().lower() in {'1', 'true', 'yes'}
    summary = get_xbox_account_summary(validate=validate)
    return jsonify({'success': True, **summary})


@api_xbox_bp.route('/api/xbox/unlink', methods=['POST'])
def unlink_xbox_account():
    """Remove the stored Xbox account credentials."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    Settings.set_value(XBOX_REFRESH_TOKEN_KEY, '')
    Settings.set_value(XBOX_LINKED_AT_KEY, '')
    return jsonify({'success': True, 'message': 'Xbox Account unlinked.'})


@api_xbox_bp.route('/api/xbox/import-device', methods=['POST'])
def import_device():
    """Enroll a chosen Xbox console as an AgentDevice."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    payload = request.get_json() or {}
    device_id = payload.get('device_id')
    display_name = payload.get('name') or "Xbox Console"

    if not device_id:
        return jsonify({'success': False, 'message': 'Device ID is required'}), 400

    existing = AgentDevice.query.get(device_id)
    if existing:
        return jsonify({'success': False, 'message': 'Device is already enrolled'}), 400

    device = AgentDevice(
        system_id=device_id,
        system_hostname=display_name,
        platform='xbox',
        status='approved'
    )
    db.session.add(device)
    db.session.commit()

    try:
        from app import task_manager
        task_manager.sync_xbox_devices(force=True)
    except Exception as exc:
        _LOGGER.warning("Initial Xbox sync after import failed: %s", exc)

    return jsonify({'success': True, 'message': f'Console {display_name} enrolled successfully!'})


@api_xbox_bp.route('/api/xbox/sync', methods=['POST'])
def sync_xbox_now():
    """Force an immediate Xbox cloud sync for enrolled consoles."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    session_token = Settings.get_value(XBOX_REFRESH_TOKEN_KEY)
    if not session_token:
        return jsonify({'success': False, 'message': 'Xbox Account is not linked'}), 400

    try:
        from app import task_manager
        task_manager.sync_xbox_devices(force=True)
        return jsonify({'success': True, 'message': 'Xbox cloud sync completed.'})
    except Exception as exc:
        _LOGGER.error("Manual Xbox sync failed: %s", exc)
        return jsonify({'success': False, 'message': str(exc)}), 500
