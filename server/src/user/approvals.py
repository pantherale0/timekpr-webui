"""Business logic for access approval requests and policy grants."""

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from src.policy.apparmor import (
    _build_apparmor_policy_sync_payload,
    _validate_apparmor_executable_path,
    _validate_apparmor_path_pattern,
    collect_policy_allowed_packages_from_mapping,
)
from src.blocklist.helper import normalize_domain
from src.models import (
    db,
    ApprovalRequest,
    DeviceInstalledApplication,
    ManagedUserDeviceMap,
    MappingApprovalSettings,
    PolicyApprovalGrant,
)
from src.device.installed_apps import ANDROID_PACKAGE_PREFIX

_LOGGER = logging.getLogger(__name__)

INTERNAL_ANDROID_PACKAGES = {
    f'{ANDROID_PACKAGE_PREFIX}com.guardian.agent',
}


def get_session_actor():
    """Return the username of the current admin session."""
    from flask import session
    from src.common.helpers import ADMIN_USERNAME

    if not session.get('logged_in'):
        return None
    user = session.get('user')
    if isinstance(user, dict):
        username = (user.get('username') or '').strip()
        if username:
            return username
    return ADMIN_USERNAME


def _get_settings(mapping):
    return MappingApprovalSettings.query.filter_by(device_map_id=mapping.id).first()


def _settings_or_defaults(mapping):
    settings = _get_settings(mapping)
    if settings is not None:
        return settings
    return MappingApprovalSettings(
        device_map_id=mapping.id,
        app_launch_mode=MappingApprovalSettings.APP_LAUNCH_OPEN,
        domain_access_mode=MappingApprovalSettings.DOMAIN_BLOCKLIST_ONLY,
    )


def get_or_create_settings(mapping):
    """Return approval settings for a mapping, creating defaults when missing."""
    settings = _get_settings(mapping)
    if settings is None:
        settings = MappingApprovalSettings(device_map_id=mapping.id)
        db.session.add(settings)
        db.session.flush()
    return settings


def upsert_settings(mapping, app_launch_mode=None, domain_access_mode=None,
                    ai_policy_mode=None, ai_prompt_logging=None, ai_daily_time_limit=None):
    """Update per-mapping approval enforcement modes."""
    settings = get_or_create_settings(mapping)

    if app_launch_mode is not None:
        normalized = (app_launch_mode or '').strip().lower()
        if normalized not in MappingApprovalSettings.VALID_APP_LAUNCH_MODES:
            raise ValueError(f'Unsupported app_launch_mode: {app_launch_mode}')
        settings.app_launch_mode = normalized

    if domain_access_mode is not None:
        normalized = (domain_access_mode or '').strip().lower()
        if normalized not in MappingApprovalSettings.VALID_DOMAIN_ACCESS_MODES:
            raise ValueError(f'Unsupported domain_access_mode: {domain_access_mode}')
        settings.domain_access_mode = normalized

    if ai_policy_mode is not None:
        normalized = (ai_policy_mode or '').strip().lower()
        if normalized not in {'off', 'block', 'monitor', 'approve'}:
            raise ValueError(f'Unsupported ai_policy_mode: {ai_policy_mode}')
        settings.ai_policy_mode = normalized

    if ai_prompt_logging is not None:
        normalized = (ai_prompt_logging or '').strip().lower()
        if normalized not in {'metadata_only', 'full_text'}:
            raise ValueError(f'Unsupported ai_prompt_logging: {ai_prompt_logging}')
        settings.ai_prompt_logging = normalized

    if ai_daily_time_limit is not None:
        if ai_daily_time_limit == '' or ai_daily_time_limit is None:
            settings.ai_daily_time_limit = None
        else:
            try:
                val = int(ai_daily_time_limit)
                if val < 0:
                    raise ValueError('ai_daily_time_limit must be >= 0')
                settings.ai_daily_time_limit = val
            except (TypeError, ValueError):
                raise ValueError(f'Invalid ai_daily_time_limit: {ai_daily_time_limit}')

    db.session.commit()
    _trigger_policy_sync(mapping, reason='approval_settings_changed')
    return settings


def _normalize_package_target(value):
    raw = (value or '').strip()
    if not raw:
        raise ValueError('target_value is required')
    if raw.startswith(ANDROID_PACKAGE_PREFIX):
        package_name = raw[len(ANDROID_PACKAGE_PREFIX):]
    else:
        package_name = raw
    from src.policy.apparmor import _validate_android_package_name
    package_name = _validate_android_package_name(package_name)
    return f'{ANDROID_PACKAGE_PREFIX}{package_name}'


def _normalize_domain_target(value):
    return normalize_domain(value)


def _normalize_executable_target(value):
    return _validate_apparmor_executable_path(value)


def _normalize_path_pattern_target(value, linux_username='user'):
    return _validate_apparmor_path_pattern(value, linux_username)


def _infer_app_launch_target_kind(mapping, target_value):
    from src.policy.apparmor import _device_platform
    from src.models import AppPolicy

    raw = (target_value or '').strip()
    if raw.startswith(ANDROID_PACKAGE_PREFIX):
        return ApprovalRequest.TARGET_PACKAGE
    device = mapping.device if mapping else None
    if _device_platform(device) == AppPolicy.PLATFORM_ANDROID:
        return ApprovalRequest.TARGET_PACKAGE
    if raw.endswith('/**'):
        return ApprovalRequest.TARGET_PATH_PATTERN
    return ApprovalRequest.TARGET_EXECUTABLE


def normalize_approval_target(target_kind, target_value, linux_username='user'):
    if target_kind == ApprovalRequest.TARGET_PACKAGE:
        return _normalize_package_target(target_value)
    if target_kind == ApprovalRequest.TARGET_EXECUTABLE:
        return _normalize_executable_target(target_value)
    if target_kind == ApprovalRequest.TARGET_PATH_PATTERN:
        return _normalize_path_pattern_target(target_value, linux_username)
    if target_kind == ApprovalRequest.TARGET_DOMAIN:
        return _normalize_domain_target(target_value)
    raise ValueError(f'Unsupported target_kind: {target_kind}')


def _package_name_from_target(target_value):
    value = (target_value or '').strip()
    if value.startswith(ANDROID_PACKAGE_PREFIX):
        return value[len(ANDROID_PACKAGE_PREFIX):]
    return value


def _resolve_mapping(system_id, linux_username):
    username = (linux_username or '').strip()
    if not username:
        raise ValueError('linux_username is required')
    mapping = ManagedUserDeviceMap.query.filter_by(
        system_id=system_id,
        linux_username=username,
    ).first()
    if mapping is None:
        raise ValueError(f'No mapping for {username} on device {system_id}')
        
    from src.models import AgentDevice
    device = AgentDevice.query.get(system_id)
    if device and mapping.managed_user and mapping.managed_user.household_id != device.household_id:
        raise ValueError(
            f'Household mismatch: child profile {mapping.managed_user.username} is in household '
            f'{mapping.managed_user.household_id}, but device is in household {device.household_id}'
        )
    return mapping


def _parse_access_request_from_alert(normalized_alert):
    event_type = normalized_alert.get('event_type')
    details = normalized_alert.get('details') or {}
    if not isinstance(details, dict):
        details = {}

    if event_type == 'access_requested':
        request_type = (details.get('request_type') or '').strip().lower()
        target_kind = (details.get('target_kind') or '').strip().lower()
        target_value = details.get('target_value')
        display_label = (details.get('display_label') or '').strip()
    elif event_type == 'app_blocked' and details.get('reason') == 'not_approved':
        request_type = ApprovalRequest.REQUEST_APP_LAUNCH
        target_value = details.get('executable_path') or details.get('target_value')
        display_label = (details.get('application_name') or '').strip()
        explicit_kind = (details.get('target_kind') or '').strip().lower()
        if explicit_kind in ApprovalRequest.VALID_TARGET_KINDS:
            target_kind = explicit_kind
        elif (target_value or '').strip().startswith(ANDROID_PACKAGE_PREFIX):
            target_kind = ApprovalRequest.TARGET_PACKAGE
        elif (target_value or '').strip().startswith('/'):
            target_kind = ApprovalRequest.TARGET_EXECUTABLE
        elif (target_value or '').strip().endswith('/**'):
            target_kind = ApprovalRequest.TARGET_PATH_PATTERN
        else:
            target_kind = ApprovalRequest.TARGET_PACKAGE
    else:
        return None

    if request_type not in ApprovalRequest.VALID_REQUEST_TYPES:
        raise ValueError(f'Unsupported request_type: {request_type}')
    if target_kind not in ApprovalRequest.VALID_TARGET_KINDS:
        raise ValueError(f'Unsupported target_kind: {target_kind}')

    linux_username = (normalized_alert.get('linux_username') or '').strip() or 'user'
    normalized_target = normalize_approval_target(
        target_kind,
        target_value,
        linux_username=linux_username,
    )
    if not display_label:
        if target_kind == ApprovalRequest.TARGET_PACKAGE:
            display_label = _package_name_from_target(normalized_target)
        else:
            display_label = normalized_target

    return {
        'request_type': request_type,
        'target_kind': target_kind,
        'target_value': normalized_target,
        'display_label': display_label[:120],
        'details': details,
    }


def ingest_access_request(system_id, normalized_alert, source_alert_id=None):
    """Create or refresh a pending approval request from an agent alert."""
    parsed = _parse_access_request_from_alert(normalized_alert)
    if parsed is None:
        return None

    mapping = _resolve_mapping(system_id, normalized_alert.get('linux_username'))
    now = datetime.now(timezone.utc)

    existing = ApprovalRequest.query.filter_by(
        device_map_id=mapping.id,
        request_type=parsed['request_type'],
        target_value=parsed['target_value'],
        status=ApprovalRequest.STATUS_PENDING,
    ).first()

    if existing is not None:
        existing.requested_at = now
        existing.display_label = parsed['display_label']
        existing.source_alert_id = source_alert_id
        existing.details_json = json.dumps(parsed['details'], sort_keys=True)
        db.session.commit()
        request_row = existing
    else:
        request_row = ApprovalRequest(
            device_map_id=mapping.id,
            request_type=parsed['request_type'],
            target_kind=parsed['target_kind'],
            target_value=parsed['target_value'],
            display_label=parsed['display_label'],
            status=ApprovalRequest.STATUS_PENDING,
            requested_at=now,
            source_alert_id=source_alert_id,
            details_json=json.dumps(parsed['details'], sort_keys=True),
        )
        db.session.add(request_row)
        db.session.commit()

    from src.common.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('approval_requested')
    return request_row


def ingest_dialogue_flag_alert(system_id, normalized_alert, source_alert_id=None):
    """Create a pending dialogue flag request from a websocket agent alert."""
    event_type = normalized_alert.get('event_type')
    if event_type not in ('dialogue_flag', 'sentiment_breach'):
        raise ValueError(f"Invalid event type: {event_type}")

    linux_username = (normalized_alert.get('linux_username') or '').strip() or 'user'
    mapping = _resolve_mapping(system_id, linux_username)
    now = datetime.now(timezone.utc)

    details = normalized_alert.get('details') or {}
    if not isinstance(details, dict):
        details = {}

    platform = (details.get('platform') or 'unknown').strip()
    target_value = platform
    display_label = f"Conversation Alert on {platform.capitalize()}"

    request_row = ApprovalRequest(
        device_map_id=mapping.id,
        request_type=event_type,
        target_kind=ApprovalRequest.TARGET_DIALOGUE,
        target_value=target_value,
        display_label=display_label,
        status=ApprovalRequest.STATUS_PENDING,
        requested_at=now,
        source_alert_id=source_alert_id,
        details_json=json.dumps(details, sort_keys=True),
    )
    db.session.add(request_row)
    db.session.commit()

    from src.common.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('approval_requested')
    return request_row


def list_pending_requests(
    status=None,
    request_type=None,
    managed_user_id=None,
    limit=50,
    active_household_id=None,
    parent_account_id=None,
):
    """Return approval requests ordered by requested_at descending."""
    query = ApprovalRequest.query

    if status:
        query = query.filter_by(status=status.strip().lower())
    else:
        query = query.filter_by(status=ApprovalRequest.STATUS_PENDING)

    if request_type:
        query = query.filter_by(request_type=request_type.strip().lower())

    if managed_user_id is not None:
        query = query.join(ManagedUserDeviceMap).filter(
            ManagedUserDeviceMap.managed_user_id == int(managed_user_id),
        )

    # Scoping filter
    if active_household_id is not None or parent_account_id is not None:
        from src.models import ManagedUser, ManagedUserDeviceMap, ManagedUserShare, db
        if managed_user_id is None:
            query = query.join(ManagedUserDeviceMap)
        query = query.join(ManagedUser, ManagedUser.id == ManagedUserDeviceMap.managed_user_id)
        
        filters = []
        if active_household_id is not None:
            if isinstance(active_household_id, (list, tuple)):
                if active_household_id:
                    filters.append(ManagedUser.household_id.in_(active_household_id))
            else:
                filters.append(ManagedUser.household_id == active_household_id)
        if parent_account_id is not None:
            shared_user_ids_subquery = db.session.query(ManagedUserShare.managed_user_id).filter(
                ManagedUserShare.parent_account_id == parent_account_id
            ).subquery()
            filters.append(ManagedUser.id.in_(shared_user_ids_subquery))
            
        if filters:
            from sqlalchemy import or_
            query = query.filter(or_(*filters))

    return (
        query.order_by(ApprovalRequest.requested_at.desc())
        .limit(max(1, min(int(limit or 50), 200)))
        .all()
    )


def build_request_summary(request_row):
    """Serialize an approval request for API and dashboard consumers."""
    mapping = request_row.device_map
    managed_user = mapping.managed_user if mapping else None
    device = mapping.device if mapping else None
    return {
        'id': request_row.id,
        'status': request_row.status,
        'request_type': request_row.request_type,
        'target_kind': request_row.target_kind,
        'target_value': request_row.target_value,
        'display_label': request_row.display_label,
        'requested_at': request_row.requested_at.isoformat() if request_row.requested_at else None,
        'decided_at': request_row.decided_at.isoformat() if request_row.decided_at else None,
        'decided_by': request_row.decided_by,
        'denial_reason': request_row.denial_reason,
        'managed_user_id': managed_user.id if managed_user else None,
        'managed_username': managed_user.username if managed_user else None,
        'device_map_id': mapping.id if mapping else None,
        'system_id': mapping.system_id if mapping else None,
        'linux_username': mapping.linux_username if mapping else None,
        'device_label': device.format_display_name() if device else None,
    }


def build_pending_approvals_snapshot(limit=5, active_household_id=None, parent_account_id=None):
    """Build dashboard pending approval counts and preview items."""
    pending = list_pending_requests(limit=200, active_household_id=active_household_id, parent_account_id=parent_account_id)
    by_user = {}
    items = []

    for request_row in pending:
        summary = build_request_summary(request_row)
        user_id = summary.get('managed_user_id')
        if user_id is not None:
            by_user[str(user_id)] = by_user.get(str(user_id), 0) + 1
        if len(items) < limit:
            items.append({
                'id': summary['id'],
                'managed_user_id': summary['managed_user_id'],
                'username': summary['managed_username'],
                'request_type': summary['request_type'],
                'display_label': summary['display_label'],
                'requested_at': summary['requested_at'],
            })

    return {
        'total': len(pending),
        'by_user': by_user,
        'items': items,
    }


def _get_or_create_active_grant(mapping, grant_type, target_kind, target_value, display_label,
                              created_by=None, source_request_id=None):
    existing = PolicyApprovalGrant.query.filter_by(
        device_map_id=mapping.id,
        grant_type=grant_type,
        target_value=target_value,
        status=PolicyApprovalGrant.STATUS_ACTIVE,
    ).first()
    if existing is not None:
        existing.display_label = display_label
        existing.created_by = created_by
        if source_request_id is not None:
            existing.source_request_id = source_request_id
        return existing

    grant = PolicyApprovalGrant(
        device_map_id=mapping.id,
        grant_type=grant_type,
        target_kind=target_kind,
        target_value=target_value,
        display_label=display_label,
        status=PolicyApprovalGrant.STATUS_ACTIVE,
        created_by=created_by,
        source_request_id=source_request_id,
    )
    db.session.add(grant)
    return grant


def approve_request(request_id, decided_by):
    """Approve a pending request and create or refresh a permanent grant."""
    request_row = ApprovalRequest.query.get(request_id)
    if request_row is None:
        raise ValueError('Approval request not found')
    if request_row.status != ApprovalRequest.STATUS_PENDING:
        raise ValueError(f'Request is not pending (status: {request_row.status})')

    mapping = request_row.device_map
    now = datetime.now(timezone.utc)
    grant_type = (
        PolicyApprovalGrant.GRANT_APP_LAUNCH
        if request_row.request_type == ApprovalRequest.REQUEST_APP_LAUNCH
        else PolicyApprovalGrant.GRANT_DOMAIN_ACCESS
    )

    _get_or_create_active_grant(
        mapping,
        grant_type=grant_type,
        target_kind=request_row.target_kind,
        target_value=request_row.target_value,
        display_label=request_row.display_label,
        created_by=decided_by,
        source_request_id=request_row.id,
    )

    request_row.status = ApprovalRequest.STATUS_APPROVED
    request_row.decided_at = now
    request_row.decided_by = decided_by
    request_row.denial_reason = None
    db.session.commit()

    _trigger_policy_sync(mapping, reason='approval_granted')
    from src.common.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('approval_resolved')
    return request_row


def deny_request(request_id, decided_by, reason=None):
    """Deny a pending approval request."""
    request_row = ApprovalRequest.query.get(request_id)
    if request_row is None:
        raise ValueError('Approval request not found')
    if request_row.status != ApprovalRequest.STATUS_PENDING:
        raise ValueError(f'Request is not pending (status: {request_row.status})')

    request_row.status = ApprovalRequest.STATUS_DENIED
    request_row.decided_at = datetime.now(timezone.utc)
    request_row.decided_by = decided_by
    request_row.denial_reason = (reason or '').strip() or None
    db.session.commit()

    from src.common.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('approval_resolved')
    return request_row


def create_grant(mapping, grant_type, target_kind, target_value, display_label, created_by):
    """Create a proactive grant without a pending request (parent pre-approval)."""
    if grant_type not in PolicyApprovalGrant.VALID_GRANT_TYPES:
        raise ValueError(f'Unsupported grant_type: {grant_type}')
    if not target_kind and grant_type == PolicyApprovalGrant.GRANT_APP_LAUNCH:
        target_kind = _infer_app_launch_target_kind(mapping, target_value)
    if target_kind not in PolicyApprovalGrant.VALID_TARGET_KINDS:
        raise ValueError(f'Unsupported target_kind: {target_kind}')

    normalized_target = normalize_approval_target(
        target_kind,
        target_value,
        linux_username=mapping.linux_username,
    )
    label = (display_label or '').strip()
    if not label:
        if target_kind == PolicyApprovalGrant.TARGET_PACKAGE:
            label = _package_name_from_target(normalized_target)
        else:
            label = normalized_target

    grant = _get_or_create_active_grant(
        mapping,
        grant_type=grant_type,
        target_kind=target_kind,
        target_value=normalized_target,
        display_label=label[:120],
        created_by=created_by,
    )
    db.session.commit()
    _trigger_policy_sync(mapping, reason='approval_grant_created')
    from src.common.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('approval_resolved')
    return grant


def revoke_grant(grant_id, revoked_by):
    """Revoke an active permanent grant."""
    grant = PolicyApprovalGrant.query.get(grant_id)
    if grant is None:
        raise ValueError('Approval grant not found')
    if grant.status != PolicyApprovalGrant.STATUS_ACTIVE:
        raise ValueError(f'Grant is not active (status: {grant.status})')

    grant.status = PolicyApprovalGrant.STATUS_REVOKED
    grant.revoked_at = datetime.now(timezone.utc)
    grant.revoked_by = revoked_by
    mapping = grant.device_map
    db.session.commit()

    _trigger_policy_sync(mapping, reason='approval_grant_revoked')
    from src.common.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('approval_resolved')
    return grant


def active_grants_for_mapping(mapping, grant_type=None):
    query = PolicyApprovalGrant.query.filter_by(
        device_map_id=mapping.id,
        status=PolicyApprovalGrant.STATUS_ACTIVE,
    )
    if grant_type:
        query = query.filter_by(grant_type=grant_type)
    return query.order_by(PolicyApprovalGrant.display_label.asc()).all()


def build_grant_summary(grant):
    return {
        'id': grant.id,
        'grant_type': grant.grant_type,
        'target_kind': grant.target_kind,
        'target_value': grant.target_value,
        'display_label': grant.display_label,
        'status': grant.status,
        'created_at': grant.created_at.isoformat() if grant.created_at else None,
        'created_by': grant.created_by,
        'source_request_id': grant.source_request_id,
    }


def compute_approval_revision_hash(mapping):
    """Hash approval settings and active grants for policy sync comparisons."""
    settings = _settings_or_defaults(mapping)
    app_launch_mode = settings.app_launch_mode
    domain_access_mode = settings.domain_access_mode

    grants = active_grants_for_mapping(mapping)
    grant_entries = [
        {
            'grant_type': grant.grant_type,
            'target_kind': grant.target_kind,
            'target_value': grant.target_value,
        }
        for grant in grants
    ]
    payload = {
        'app_launch_mode': app_launch_mode,
        'domain_access_mode': domain_access_mode,
        'grants': sorted(grant_entries, key=lambda item: (item['grant_type'], item['target_value'])),
    }
    digest_source = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(digest_source.encode('utf-8')).hexdigest()


def build_app_approval_sync_extras(mapping):
    """Build the approval_policy object for sync_apparmor_policy."""
    settings = _settings_or_defaults(mapping)
    grant_approved = {
        _package_name_from_target(grant.target_value)
        for grant in active_grants_for_mapping(mapping, PolicyApprovalGrant.GRANT_APP_LAUNCH)
    }
    policy_allowed = collect_policy_allowed_packages_from_mapping(mapping)
    approved_packages = sorted(grant_approved | policy_allowed)

    if settings.app_launch_mode == MappingApprovalSettings.APP_LAUNCH_OPEN:
        return None

    policies_list, _ = _build_apparmor_policy_sync_payload(mapping)
    blocked_from_rules = sorted({
        _package_name_from_target(rule['executable_path'])
        for rule in policies_list
        if rule.get('preset') == 'blocked'
    })

    if settings.app_launch_mode == MappingApprovalSettings.APP_LAUNCH_BLOCKLIST:
        effective_blocked = sorted(set(blocked_from_rules) - set(approved_packages))
        return {
            'app_launch_mode': settings.app_launch_mode,
            'approved_packages': approved_packages,
            'blocked_packages': effective_blocked,
        }

    installed = DeviceInstalledApplication.query.filter_by(
        system_id=mapping.system_id,
        linux_username=mapping.linux_username,
        is_present=True,
    ).all()
    installed_packages = sorted({
        _package_name_from_target(app.identifier)
        for app in installed
        if app.identifier and app.identifier not in INTERNAL_ANDROID_PACKAGES
    })
    effective_blocked = sorted(set(installed_packages) - set(approved_packages))
    return {
        'app_launch_mode': settings.app_launch_mode,
        'approved_packages': approved_packages,
        'blocked_packages': effective_blocked,
    }


def build_domain_allowed_domains(mapping):
    """Return active domain grants when approval-on-block mode is enabled."""
    settings = _settings_or_defaults(mapping)
    if settings.domain_access_mode != MappingApprovalSettings.DOMAIN_APPROVAL_ON_BLOCK:
        return []
    return sorted({
        grant.target_value
        for grant in active_grants_for_mapping(mapping, PolicyApprovalGrant.GRANT_DOMAIN_ACCESS)
    })


def build_full_app_policy_sync_payload(mapping):
    """Return policies list and optional approval_policy for agent sync."""
    policies_list, skipped = _build_apparmor_policy_sync_payload(mapping)
    approval_policy = build_app_approval_sync_extras(mapping)
    return policies_list, skipped, approval_policy


def grant_status_for_apps(mapping, installed_apps):
    """Map app identifier to approval status for profile UI."""
    approved_targets = {
        grant.target_value
        for grant in active_grants_for_mapping(mapping, PolicyApprovalGrant.GRANT_APP_LAUNCH)
    }
    pending_targets = {
        row.target_value
        for row in ApprovalRequest.query.filter_by(
            device_map_id=mapping.id,
            request_type=ApprovalRequest.REQUEST_APP_LAUNCH,
            status=ApprovalRequest.STATUS_PENDING,
        ).all()
    }
    grant_by_target = {
        grant.target_value: grant
        for grant in active_grants_for_mapping(mapping, PolicyApprovalGrant.GRANT_APP_LAUNCH)
    }

    status_map = {}
    for app in installed_apps or []:
        identifier = app.get('identifier') if isinstance(app, dict) else app.identifier
        if not identifier:
            continue
        if identifier in approved_targets:
            status = 'approved'
            grant_id = grant_by_target.get(identifier).id if grant_by_target.get(identifier) else None
        elif identifier in pending_targets:
            status = 'pending'
            grant_id = None
        else:
            status = 'none'
            grant_id = None
        status_map[identifier] = {'status': status, 'grant_id': grant_id}
    return status_map


def push_approval_policies_after_inventory(system_id, linux_username):
    """Re-push allowlist approval policy after inventory sync updates installed apps."""
    username = (linux_username or '').strip()
    if not username:
        return

    mappings = ManagedUserDeviceMap.query.filter_by(
        system_id=system_id,
        linux_username=username,
    ).all()
    for mapping in mappings:
        settings = _settings_or_defaults(mapping)
        if settings.app_launch_mode != MappingApprovalSettings.APP_LAUNCH_ALLOWLIST:
            continue
        try:
            push_mapping_app_policy(mapping)
        except (OSError, RuntimeError, SQLAlchemyError, ValueError) as exc:
            _LOGGER.warning(
                'Failed to push approval policy after inventory for mapping %s: %s',
                mapping.id,
                exc,
            )


def push_mapping_app_policy(mapping):
    """Push effective app policy including approval overlay to the agent if online."""
    from src.agent.helper import AgentClient, AgentConnectionManager

    if not AgentConnectionManager.is_online(mapping.system_id):
        from src.agent.pending_commands import enqueue_policy_snapshot

        try:
            enqueue_policy_snapshot(
                mapping.system_id,
                'sync_apparmor_policy',
                mapping.linux_username,
            )
            return True, 'Queued for reconnect'
        except ValueError as exc:
            return False, str(exc)

    policies_list, _, approval_policy = build_full_app_policy_sync_payload(mapping)
    agent = AgentClient(system_id=mapping.system_id)
    return agent.sync_apparmor_policy(
        mapping.linux_username,
        policies_list,
        approval_policy=approval_policy,
    )


def _trigger_policy_sync(mapping, reason='approval_changed'):
    """Notify domain policy refresh and push app policy when possible."""
    from app import task_manager

    try:
        task_manager.notify_domain_policy_hint(
            mapping.device.household_id,
            system_ids={mapping.system_id},
            reason=reason,
        )
    except (ImportError, RuntimeError, AttributeError) as exc:
        _LOGGER.warning('Failed to notify domain policy hint: %s', exc)

    try:
        push_mapping_app_policy(mapping)
    except (OSError, RuntimeError, SQLAlchemyError, ValueError) as exc:
        _LOGGER.warning(
            'Failed to push app approval policy for mapping %s: %s',
            mapping.id,
            exc,
        )
