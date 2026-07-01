import os
import json
import logging
import pytz
from datetime import timezone
from flask import session, request
from src.models import AgentDevice

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
    from src.auth.session_lifecycle import get_oidc_expires_at, session_warn_seconds

    expires_at = get_oidc_expires_at(session) if session.get('logged_in') else None
    return {
        'oidc_enabled': oidc_helper.is_enabled,
        'session_user': session.get('user'),
        'session_expires_at': int(expires_at) if expires_at is not None else None,
        'session_warn_seconds': session_warn_seconds(),
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
    from src.blocklist.marketplace import load_marketplace_presets
    from src.policy.presets import get_matrix_metadata_for_ui
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
    from src.models import db, HouseholdParentMembership, ManagedUser, ManagedUserShare
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
            default_val = True if required_perm == 'can_view_screentime' else False
            return bool(perms.get(required_perm, default_val))
        return True # Default structural membership access
        
    # 2. Check if the child is individually shared with this parent
    share = ManagedUserShare.query.filter_by(parent_account_id=parent_id, managed_user_id=child_id).first()
    if share:
        perms = share.permissions_json or {}
        if not required_perm:
            return True
        default_val = True if required_perm == 'can_view_screentime' else False
        return bool(perms.get(required_perm, default_val))
        
    return False


def get_accessible_child_ids(parent_id: int) -> set:
    """Return a set of child profile IDs that the parent has permission to access."""
    from src.models import ManagedUser, HouseholdParentMembership, ManagedUserShare
    if parent_id is None:
        return {u.id for u in ManagedUser.query.all()}

    accessible_ids = set()

    # 1. Children from parent's households
    memberships = HouseholdParentMembership.query.filter_by(parent_account_id=parent_id).all()
    hh_ids = [m.household_id for m in memberships]
    if hh_ids:
        households_children = ManagedUser.query.filter(ManagedUser.household_id.in_(hh_ids)).all()
        for child in households_children:
            accessible_ids.add(child.id)

    # 2. Children shared individually
    shares = ManagedUserShare.query.filter_by(parent_account_id=parent_id).all()
    for share in shares:
        accessible_ids.add(share.managed_user_id)

    return accessible_ids


def parent_has_access_to_device(parent_id: int, system_id: str) -> bool:
    """Verify if a parent has access to a specific device."""
    from src.models import db, HouseholdParentMembership, AgentDevice
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
    from flask import abort

    parent_id = resolve_session_parent_id()
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
    parent_id = resolve_session_parent_id()
    # No parent identity → single-tenant local mode, allow through.
    if not parent_id:
        return
    if not parent_has_access_to_device(parent_id, system_id):
        abort(403)


def resolve_session_parent_id():
    """Return the authenticated parent account id, with local-admin fallback."""
    from flask import session
    from src.models import ParentAccount

    parent_id = session.get('parent_account_id')
    if not parent_id and session.get('logged_in'):
        parent = ParentAccount.query.filter_by(email='admin@local').first()
        parent_id = parent.id if parent else None
    return parent_id


def get_parent_household_ids(parent_id: int) -> set:
    """Household ids the parent belongs to."""
    if not parent_id:
        return set()
    from src.models import ParentAccount

    parent = ParentAccount.query.get(parent_id)
    if not parent:
        return set()
    return {membership.household_id for membership in parent.memberships if membership.household_id}


def resolve_active_household_for_write(parent_id: int):
    """Pick the household to associate with a new catalog object."""
    from flask import session

    if not parent_id:
        return None
    household_ids = get_parent_household_ids(parent_id)
    if not household_ids:
        return None
    active = session.get('active_household_id')
    if active in household_ids:
        return active
    return next(iter(household_ids))


def check_parent_household_membership(parent_id: int, household_id: int):
    """Raise 403 when the parent is not a member of the household."""
    from flask import abort

    if not parent_id:
        return
    if household_id not in get_parent_household_ids(parent_id):
        abort(403)


def check_parent_can_link_device(child_id: int, system_id: str, required_perm: str = 'can_manage_policies'):
    """Verify parent may attach a device mapping for this child/device pair."""
    from flask import abort
    from src.models import ManagedUser, AgentDevice

    check_parent_child_access(child_id, required_perm)
    check_parent_device_access(system_id)
    child = ManagedUser.query.get(child_id)
    device = AgentDevice.query.get(system_id)
    if child and device and child.household_id and device.household_id:
        if child.household_id != device.household_id:
            abort(403)


def scope_alerts_query_for_parent(query, parent_id: int):
    """Restrict an AgentAlert query to devices mapped to accessible children."""
    from src.models import AgentAlert, ManagedUserDeviceMap

    if not parent_id:
        return query

    allowed_child_ids = get_accessible_child_ids(parent_id)
    if not allowed_child_ids:
        return query.filter(False)

    allowed_system_ids = {
        mapping.system_id
        for mapping in ManagedUserDeviceMap.query.filter(
            ManagedUserDeviceMap.managed_user_id.in_(allowed_child_ids),
        ).all()
    }
    if not allowed_system_ids:
        return query.filter(False)
    return query.filter(AgentAlert.system_id.in_(allowed_system_ids))


def check_parent_alert_filter_access(
    parent_id: int,
    system_id: str = None,
    managed_user_id: int = None,
):
    """Raise 403 when alert filter params reference inaccessible objects."""
    from flask import abort

    if not parent_id:
        return
    if system_id and not parent_has_access_to_device(parent_id, system_id):
        abort(403)
    if managed_user_id is not None and not parent_has_access_to_child(parent_id, int(managed_user_id)):
        abort(403)


def check_parent_screenshot_access(screenshot_db_id: int):
    """Load a screenshot only when the parent may access its device."""
    from flask import abort
    from src.device.screenshots import get_screenshot_by_id

    screenshot = get_screenshot_by_id(screenshot_db_id)
    if screenshot is None:
        abort(404)
    check_parent_device_access(screenshot.system_id)
    return screenshot


def check_parent_grant_access(grant_id: int, required_perm: str = 'can_manage_policies'):
    """Raise 403/404 when the parent cannot manage an approval grant."""
    from flask import abort
    from src.models import PolicyApprovalGrant

    grant = PolicyApprovalGrant.query.get(grant_id)
    if grant is None:
        abort(404)
    mapping = grant.device_map
    if mapping is None:
        abort(404)
    check_parent_child_access(mapping.managed_user_id, required_perm)
    check_parent_device_access(mapping.system_id)


def parent_can_manage_blocklist_source(parent_id: int, source_id: int) -> bool:
    """True when the parent may mutate a non-marketplace blocklist source."""
    from src.models import BlocklistSource, ManagedUserBlocklistAssignment

    source = BlocklistSource.query.get(source_id)
    if source is None or source.is_marketplace or source.preset_id:
        return False
    if not parent_id:
        return True
    if source.household_id is not None:
        return source.household_id in get_parent_household_ids(parent_id)

    accessible_child_ids = get_accessible_child_ids(parent_id)
    assignments = ManagedUserBlocklistAssignment.query.filter_by(source_id=source_id).all()
    if not assignments:
        return False
    return any(assignment.managed_user_id in accessible_child_ids for assignment in assignments)


def check_parent_blocklist_source_access(source_id: int):
    """Raise 403 when the parent cannot manage a blocklist source."""
    from flask import abort

    parent_id = resolve_session_parent_id()
    if not parent_id:
        return
    if not parent_can_manage_blocklist_source(parent_id, source_id):
        abort(403)


def parent_can_manage_app_policy(parent_id: int, policy_id: int) -> bool:
    """True when the parent may mutate an app policy."""
    from src.models import AppPolicy, ManagedUserAppPolicyAssignment

    policy = AppPolicy.query.get(policy_id)
    if policy is None:
        return False
    if not parent_id:
        return True
    if policy.household_id is not None:
        return policy.household_id in get_parent_household_ids(parent_id)

    accessible_child_ids = get_accessible_child_ids(parent_id)
    assignments = ManagedUserAppPolicyAssignment.query.filter_by(policy_id=policy_id).all()
    if not assignments:
        return False
    return any(assignment.managed_user_id in accessible_child_ids for assignment in assignments)


def check_parent_app_policy_access(policy_id: int):
    """Raise 403 when the parent cannot manage an app policy."""
    from flask import abort

    parent_id = resolve_session_parent_id()
    if not parent_id:
        return
    if not parent_can_manage_app_policy(parent_id, policy_id):
        abort(403)


def filter_pending_devices_for_parent(parent_id: int):
    """Return pending devices visible to the authenticated parent."""
    from src.models import AgentDevice

    if not parent_id:
        return AgentDevice.query.filter_by(status='pending').all()
    household_ids = get_parent_household_ids(parent_id)
    if not household_ids:
        return []
    return AgentDevice.query.filter(
        AgentDevice.status == 'pending',
        AgentDevice.household_id.in_(household_ids),
    ).all()


