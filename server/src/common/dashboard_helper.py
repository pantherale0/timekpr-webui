"""Shared helpers for building dashboard snapshots."""

from src.models import ManagedUser
from src.agent.helper import AgentConnectionManager
from src.common.helpers import _format_seconds, localtime_filter


def _pending_adjustment_label(user):
    if user.pending_time_adjustment is None or user.pending_time_operation is None:
        return None
    minutes = user.pending_time_adjustment // 60
    return f"{user.pending_time_operation}{minutes} minutes"


def _schedule_is_synced(user):
    if not user.weekly_schedule:
        return True
    return bool(user.weekly_schedule.is_synced)


def _build_user_entry(user, active_household_id=None, has_multiple_households=False, parent_id=None):
    usage_data = user.get_recent_usage(days=7)
    mapping_count = len(user.device_mappings)
    online_mapping_count = sum(
        1 for mapping in user.device_mappings if AgentConnectionManager.is_online(mapping.system_id)
    )
    valid_mapping_count = sum(1 for mapping in user.device_mappings if mapping.is_valid)
    time_left_formatted = _format_seconds(user.get_effective_time_left_seconds())

    last_checked_display = None
    if user.last_checked:
        local_dt = localtime_filter(user.last_checked)
        last_checked_display = local_dt.strftime('%H:%M') if local_dt else None

    is_shared = False
    if active_household_id is not None:
        if isinstance(active_household_id, (list, tuple)):
            is_shared = user.household_id not in active_household_id
        else:
            is_shared = user.household_id != active_household_id

    from src.common.helpers import parent_has_access_to_child
    can_manage_screentime = parent_has_access_to_child(parent_id, user.id, 'can_manage_screentime') if parent_id is not None else True
    can_manage_policies = parent_has_access_to_child(parent_id, user.id, 'can_manage_policies') if parent_id is not None else True

    return {
        'id': user.id,
        'username': user.username,
        'is_online': online_mapping_count > 0,
        'mapping_count': mapping_count,
        'online_mapping_count': online_mapping_count,
        'valid_mapping_count': valid_mapping_count,
        'last_checked': user.last_checked.isoformat() if user.last_checked else None,
        'last_checked_display': last_checked_display,
        'usage_data': usage_data,
        'time_left': time_left_formatted,
        'pending_adjustment': _pending_adjustment_label(user),
        'schedule_is_synced': _schedule_is_synced(user),
        'policy_age_bracket': user.policy_age_bracket,
        'policy_maturity_level': user.policy_maturity_level,
        'is_shared': is_shared,
        'household_name': user.household.name if user.household else None,
        'has_multiple_households': has_multiple_households,
        'can_manage_screentime': can_manage_screentime,
        'can_manage_policies': can_manage_policies,
    }


def build_dashboard_snapshot(active_household_id=None, parent_account_id=None):
    """Build dashboard data for HTML rendering and JSON/SSE payloads."""
    users = []
    
    if active_household_id:
        if isinstance(active_household_id, (list, tuple)):
            users.extend(ManagedUser.query.filter(ManagedUser.household_id.in_(active_household_id)).all())
        else:
            users.extend(ManagedUser.query.filter_by(household_id=active_household_id).all())
        
    if parent_account_id:
        from src.models import ManagedUserShare
        shared_users = ManagedUser.query.join(
            ManagedUserShare, ManagedUserShare.managed_user_id == ManagedUser.id
        ).filter(
            ManagedUserShare.parent_account_id == parent_account_id
        ).all()
        
        existing_ids = {u.id for u in users}
        for su in shared_users:
            if su.id not in existing_ids:
                users.append(su)

    if not active_household_id and not parent_account_id:
        users = ManagedUser.query.all()

    has_multiple_households = False
    if parent_account_id:
        from src.models import ParentAccount
        parent = ParentAccount.query.get(parent_account_id)
        if parent:
            has_multiple_households = len(parent.memberships) > 1

    pending_adjustments = {}
    user_data = []

    for user in users:
        entry = _build_user_entry(user, active_household_id=active_household_id, has_multiple_households=has_multiple_households, parent_id=parent_account_id)
        if entry['pending_adjustment']:
            pending_adjustments[str(user.id)] = entry['pending_adjustment']
        user_data.append(entry)

    users_sorted = sorted(user_data, key=lambda item: item['username'].lower())

    pending_approvals = {'total': 0, 'by_user': {}, 'items': []}
    try:
        from src.user.approvals import build_pending_approvals_snapshot
        pending_approvals = build_pending_approvals_snapshot(
            limit=5,
            active_household_id=active_household_id,
            parent_account_id=parent_account_id,
        )
    except (ImportError, RuntimeError, TypeError, ValueError):
        pass

    return {
        'users': users_sorted,
        'pending_adjustments': pending_adjustments,
        'pending_approvals': pending_approvals,
    }


def build_dashboard_json_snapshot():
    """Build a JSON-serializable dashboard snapshot."""
    from flask import has_request_context, session
    parent_account_id = session.get('parent_account_id') if has_request_context() else None

    # Resolve all parent households if logged in
    household_ids = []
    if parent_account_id:
        from src.models import ParentAccount
        p = ParentAccount.query.get(parent_account_id)
        if p:
            household_ids = [m.household_id for m in p.memberships if m.household_id]

    snapshot = build_dashboard_snapshot(household_ids, parent_account_id)
    users = []
    for user in snapshot['users']:
        users.append({
            'id': user['id'],
            'username': user['username'],
            'is_online': user['is_online'],
            'mapping_count': user['mapping_count'],
            'online_mapping_count': user['online_mapping_count'],
            'valid_mapping_count': user['valid_mapping_count'],
            'last_checked': user['last_checked'],
            'last_checked_display': user['last_checked_display'],
            'usage_data': user['usage_data'],
            'time_left': user['time_left'],
            'pending_adjustment': user['pending_adjustment'],
            'schedule_is_synced': user['schedule_is_synced'],
            'policy_age_bracket': user['policy_age_bracket'],
            'policy_maturity_level': user['policy_maturity_level'],
            'is_shared': user.get('is_shared', False),
            'household_name': user.get('household_name'),
            'has_multiple_households': user.get('has_multiple_households', False),
        })
    return {
        'users': users,
        'pending_adjustments': snapshot['pending_adjustments'],
        'pending_approvals': snapshot.get('pending_approvals', {'total': 0, 'by_user': {}, 'items': []}),
    }
