import os
import json
import logging
import pytz
from datetime import timezone
from flask import session, request
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


def inject_i18n():
    """Inject locale and translation helper into templates."""
    from flask import g
    from src.i18n.catalog import (
        DEFAULT_LOCALE,
        discover_locales,
        flatten_for_js,
        load_catalog,
        locale_label,
        t as translate,
    )

    active_locale = getattr(g, 'locale', DEFAULT_LOCALE)
    catalog = load_catalog(active_locale)
    return {
        'locale': active_locale,
        't': lambda key, **kwargs: translate(key, locale=active_locale, **kwargs),
        'available_locales': discover_locales(),
        'locale_labels': {code: locale_label(code) for code in discover_locales()},
        'js_catalog': flatten_for_js(catalog),
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


def wants_json_response() -> bool:
    """True when the client expects a JSON body instead of a redirect."""
    accept = request.headers.get('Accept', '')
    if 'application/json' in accept:
        return True
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def parent_has_access_to_child(parent_id: int, child_id: int, required_perm: str = None) -> bool:
    """Verify if a parent has access to a specific child profile."""
    from src.database import db, HouseholdParentMembership, ManagedUser, ManagedUserShare
    # 1. Check if the child belongs to a Household the parent is in
    membership = db.session.query(HouseholdParentMembership).join(
        ManagedUser, ManagedUser.household_id == HouseholdParentMembership.household_id
    ).filter(
        HouseholdParentMembership.parent_account_id == parent_id,
        ManagedUser.id == child_id
    ).first()
    
    if membership:
        perms = membership.permissions_json or {}
        if perms.get('is_owner') or perms.get('is_admin'):
            return True
        if required_perm:
            return perms.get(required_perm, False)
        return True # Default structural membership access
        
    # 2. Check if the child is individually shared with this parent
    share = ManagedUserShare.query.filter_by(parent_account_id=parent_id, managed_user_id=child_id).first()
    if share:
        perms = share.permissions_json or {}
        if not required_perm:
            return True
        return perms.get(required_perm, False)
        
    return False


def parent_has_access_to_device(parent_id: int, system_id: str) -> bool:
    """Verify if a parent has access to a specific device."""
    from src.database import db, HouseholdParentMembership, AgentDevice
    if not system_id:
        return False
    # Check if the device belongs to a Household the parent is in
    membership = db.session.query(HouseholdParentMembership).join(
        AgentDevice, AgentDevice.household_id == HouseholdParentMembership.household_id
    ).filter(
        HouseholdParentMembership.parent_account_id == parent_id,
        AgentDevice.system_id == system_id
    ).first()
    
    return membership is not None


def check_parent_child_access(child_id: int, required_perm: str = None):
    """Raise 403 if the logged in parent does not have access to the child.

    When no parent identity can be resolved (local admin mode or test environments
    where admin@local does not yet exist), access is granted. Tenant isolation only
    engages when a concrete parent_id is established.
    """
    from flask import session, abort
    from src.database import ParentAccount
    parent_id = session.get('parent_account_id')
    if not parent_id and session.get('logged_in'):
        p = ParentAccount.query.filter_by(email='admin@local').first()
        parent_id = p.id if p else None
    # No parent identity → single-tenant local mode, allow through.
    if not parent_id:
        return
    if not parent_has_access_to_child(parent_id, child_id, required_perm):
        abort(403)


def check_parent_device_access(system_id: str):
    """Raise 403 if the logged in parent does not have access to the device.

    Falls through when no parent identity is available (local admin / test mode).
    """
    from flask import session, abort
    from src.database import ParentAccount
    parent_id = session.get('parent_account_id')
    if not parent_id and session.get('logged_in'):
        p = ParentAccount.query.filter_by(email='admin@local').first()
        parent_id = p.id if p else None
    # No parent identity → single-tenant local mode, allow through.
    if not parent_id:
        return
    if not parent_has_access_to_device(parent_id, system_id):
        abort(403)


