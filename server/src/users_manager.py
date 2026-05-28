import json
import logging
from datetime import date, datetime, timezone
from src.database import db, UserTimeUsage, get_mapping_time_spent_for_day, get_mapping_time_left_for_day
from src.agent_helper import AgentConnectionManager

_LOGGER = logging.getLogger(__name__)


def _refresh_managed_user_summary(user):
    valid_mappings = [mapping for mapping in user.device_mappings if mapping.is_valid]
    user.is_valid = bool(valid_mappings)
    today = date.today()
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
        (mapping.last_checked for mapping in valid_mappings if mapping.last_checked),
        default=datetime.now(timezone.utc).replace(tzinfo=None),
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
