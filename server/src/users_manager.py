import json
import logging
from datetime import datetime, timezone
from src.database import (
    db,
    UserTimeUsage,
    ensure_offline_mapping_day_snapshot,
    get_mapping_time_left_for_day,
    get_mapping_time_spent_for_day,
    utc_today,
)
from src.agent_helper import AgentConnectionManager

_LOGGER = logging.getLogger(__name__)


def _refresh_managed_user_summary(user):
    valid_mappings = [mapping for mapping in user.device_mappings if mapping.is_valid]
    user.is_valid = bool(valid_mappings)
    today = utc_today()
    effective_daily_limit_seconds = user.get_effective_daily_limit_seconds(today)

    if not valid_mappings:
        user.last_checked = datetime.now(timezone.utc)
        user.last_config = json.dumps({
            "TIME_SPENT_DAY": 0,
            "TIME_LEFT_DAY": effective_daily_limit_seconds,
            "MAPPING_COUNT": len(user.device_mappings),
            "ONLINE_MAPPING_COUNT": 0,
        })
        return

    shared_spent = 0
    time_left_values = []
    for mapping in valid_mappings:
        if not AgentConnectionManager.is_online(mapping.system_id):
            ensure_offline_mapping_day_snapshot(
                mapping,
                today,
                effective_daily_limit_seconds,
            )
        shared_spent += get_mapping_time_spent_for_day(mapping, today)
        time_left = get_mapping_time_left_for_day(mapping, today)
        if time_left is not None:
            time_left_values.append(time_left)

    shared_time_left = (
        max(effective_daily_limit_seconds - shared_spent, 0)
        if effective_daily_limit_seconds is not None
        else (min(time_left_values) if time_left_values else None)
    )

    user.last_checked = max(
        (
            mapping.last_checked if mapping.last_checked.tzinfo is not None
            else mapping.last_checked.replace(tzinfo=timezone.utc)
            for mapping in valid_mappings if mapping.last_checked
        ),
        default=datetime.now(timezone.utc),
    )
    user.last_config = json.dumps({
        "TIME_SPENT_DAY": shared_spent,
        "TIME_LEFT_DAY": shared_time_left,
        "MAPPING_COUNT": len(user.device_mappings),
        "ONLINE_MAPPING_COUNT": sum(
            1 for mapping in user.device_mappings if AgentConnectionManager.is_online(mapping.system_id)
        ),
    })

    usage = UserTimeUsage.query.filter_by(user_id=user.id, date=today).first()
    if usage:
        usage.time_spent = shared_spent
    else:
        db.session.add(UserTimeUsage(user_id=user.id, date=today, time_spent=shared_spent))


def sync_mapping_linux_uids_from_device(device):
    """
    Match device-reported linux_users to mappings by username and update linux_uid.

    Returns system_ids whose mappings had linux_uid changes.
    """
    if device is None or not device.linux_users:
        return set()

    uid_by_username = {}
    for entry in device.linux_users:
        if not isinstance(entry, dict):
            continue
        username = (entry.get('username') or '').strip()
        uid = entry.get('uid')
        if not username or uid is None:
            continue
        try:
            uid_by_username[username.casefold()] = int(uid)
        except (TypeError, ValueError):
            continue

    if not uid_by_username:
        return set()

    updated_system_ids = set()
    for mapping in device.user_mappings:
        key = (mapping.linux_username or '').casefold()
        reported_uid = uid_by_username.get(key)
        if reported_uid is None:
            continue
        if mapping.linux_uid != reported_uid:
            mapping.linux_uid = reported_uid
            updated_system_ids.add(mapping.system_id)
    return updated_system_ids
