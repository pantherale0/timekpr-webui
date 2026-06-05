"""Shared helpers for building dashboard snapshots."""

from src.database import ManagedUser
from src.agent_helper import AgentConnectionManager
from src.helpers import _format_seconds, localtime_filter


def _pending_adjustment_label(user):
    if user.pending_time_adjustment is None or user.pending_time_operation is None:
        return None
    minutes = user.pending_time_adjustment // 60
    return f"{user.pending_time_operation}{minutes} minutes"


def _schedule_is_synced(user):
    if not user.weekly_schedule:
        return True
    return bool(user.weekly_schedule.is_synced)


def _build_user_entry(user):
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
    }


def build_dashboard_snapshot():
    """Build dashboard data for HTML rendering and JSON/SSE payloads."""
    users = ManagedUser.query.all()
    pending_adjustments = {}
    user_data = []

    for user in users:
        entry = _build_user_entry(user)
        if entry['pending_adjustment']:
            pending_adjustments[str(user.id)] = entry['pending_adjustment']
        user_data.append(entry)

    users_sorted = sorted(user_data, key=lambda item: item['username'].lower())
    return {
        'users': users_sorted,
        'pending_adjustments': pending_adjustments,
    }


def build_dashboard_json_snapshot():
    """Build a JSON-serializable dashboard snapshot."""
    snapshot = build_dashboard_snapshot()
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
        })
    return {
        'users': users,
        'pending_adjustments': snapshot['pending_adjustments'],
    }
