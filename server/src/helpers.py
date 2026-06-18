import os
import json
import logging
import pytz
from datetime import timezone
from flask import session
from src.database import AgentDevice

_LOGGER = logging.getLogger(__name__)

ADMIN_USERNAME = 'admin'


def _resolve_local_timezone(timezone_name):
    """Resolve the configured timezone, falling back to UTC when needed."""
    try:
        resolved_timezone = pytz.timezone(timezone_name)
        _LOGGER.info("Using timezone: %s", timezone_name)
        return resolved_timezone, timezone_name
    except pytz.exceptions.UnknownTimeZoneError:
        _LOGGER.warning("Unknown timezone '%s', falling back to UTC", timezone_name)
        return pytz.UTC, 'UTC'


# Setup global timezone settings matching the original configuration
TIMEZONE_STR = os.environ.get('TZ', 'UTC')
LOCAL_TIMEZONE, TIMEZONE_STR = _resolve_local_timezone(TIMEZONE_STR)


def _env_flag_enabled(key, default=False):
    raw_value = os.environ.get(key)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {'1', 'true', 'yes', 'on'}


def inject_oidc_status():
    """Inject OIDC status and session user into templates"""
    from app import oidc_helper
    return {
        'oidc_enabled': oidc_helper.is_enabled,
        'session_user': session.get('user')
    }


def localtime_filter(dt):
    """Convert UTC datetime to local timezone"""
    if dt is None:
        return None

    # If datetime is naive (no timezone info), assume it's UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=pytz.UTC)

    # Convert to local timezone
    local_dt = dt.astimezone(LOCAL_TIMEZONE)
    return local_dt


def inject_timezone():
    """Inject timezone info into all templates"""
    return {'timezone': TIMEZONE_STR}


def inject_create_profile_wizard():
    """Inject preset data for the global Create Managed Profile wizard."""
    if not session.get('logged_in'):
        return {}
    from src.marketplace_manager import load_marketplace_presets
    from src.policy_preset_manager import get_matrix_metadata_for_ui
    return {
        'policy_preset_matrix': get_matrix_metadata_for_ui(),
        'marketplace_presets': load_marketplace_presets(),
    }


def _format_seconds(seconds):
    if seconds is None:
        return "Unknown"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def _mapping_config(mapping):
    if not mapping.last_config:
        return {}
    try:
        return json.loads(mapping.last_config)
    except (TypeError, ValueError):
        return {}


def _hostname_key(hostname):
    normalized = (hostname or '').strip()
    return normalized.casefold() if normalized else None


def _build_device_label_map(devices):
    hostname_counts = {}
    for device in devices:
        key = _hostname_key(device.system_hostname)
        if key:
            hostname_counts[key] = hostname_counts.get(key, 0) + 1

    label_map = {}
    for device in devices:
        key = _hostname_key(device.system_hostname)
        label_map[device.system_id] = device.format_display_name(
            include_suffix=bool(key and hostname_counts.get(key, 0) > 1)
        )
    return label_map


def _get_device_label_map():
    return _build_device_label_map(AgentDevice.query.all())


def _device_display_label(system_id, label_map=None):
    if not system_id:
        return 'Unknown device'

    labels = label_map if label_map is not None else _get_device_label_map()
    return labels.get(system_id, system_id)


def _mapping_display_label(mapping, label_map=None):
    return f"{mapping.linux_username}@{_device_display_label(mapping.system_id, label_map)}"


def generate_parental_access_code(secure_token: str, time_step_seconds: int = 1800) -> str:
    """Generate a 6-digit TOTP code using hmac-sha256 and a time step (default 30 mins)."""
    if not secure_token:
        return "000000"
    import hmac
    import hashlib
    import struct
    import time
    key_bytes = secure_token.encode('utf-8')
    time_slot = int(time.time()) // time_step_seconds
    msg = struct.pack(">Q", time_slot)
    hm_val = hmac.new(key_bytes, msg, hashlib.sha256).digest()
    offset = hm_val[-1] & 0x0f
    binary = struct.unpack(">I", hm_val[offset:offset+4])[0] & 0x7fffffff
    otp = binary % 1000000
    return f"{otp:06d}"

