import logging
from src.models import UserDailyTimeInterval

_LOGGER = logging.getLogger(__name__)

INTERVAL_STEP_MINUTES = 15
INTERVAL_DAY_NAMES = {
    1: 'Monday',
    2: 'Tuesday',
    3: 'Wednesday',
    4: 'Thursday',
    5: 'Friday',
    6: 'Saturday',
    7: 'Sunday',
}


def _serialize_interval(interval):
    return {
        'id': interval.id,
        'day_name': interval.get_day_name(),
        'sort_order': interval.sort_order,
        'start_hour': interval.start_hour,
        'start_minute': interval.start_minute,
        'end_hour': interval.end_hour,
        'end_minute': interval.end_minute,
        'is_enabled': interval.is_enabled,
        'is_synced': interval.is_synced,
        'time_range': interval.get_time_range_string(),
        'last_synced': interval.last_synced.strftime('%Y-%m-%d %H:%M') if interval.last_synced else None,
    }


def _normalize_interval_entries(raw_entries):
    if raw_entries is None:
        return []
    if isinstance(raw_entries, dict):
        return [raw_entries]
    if not isinstance(raw_entries, list):
        raise ValueError('Each day must contain a list of intervals')
    return raw_entries


def _build_intervals_for_day(day_of_week, raw_entries):
    if day_of_week not in INTERVAL_DAY_NAMES:
        raise ValueError(f'Invalid day of week: {day_of_week}')

    interval_rows = []
    for raw_interval in _normalize_interval_entries(raw_entries):
        if not isinstance(raw_interval, dict):
            raise ValueError(f'Invalid interval payload for {INTERVAL_DAY_NAMES[day_of_week]}')

        if not bool(raw_interval.get('is_enabled', True)):
            continue

        interval_rows.append(UserDailyTimeInterval(
            day_of_week=day_of_week,
            sort_order=len(interval_rows),
            start_hour=int(raw_interval.get('start_hour', 9)),
            start_minute=int(raw_interval.get('start_minute', 0)),
            end_hour=int(raw_interval.get('end_hour', 17)),
            end_minute=int(raw_interval.get('end_minute', 0)),
            is_enabled=True,
        ))

    ordered_rows = UserDailyTimeInterval.sort_intervals(interval_rows)
    for index, interval in enumerate(ordered_rows):
        interval.sort_order = index

    if not UserDailyTimeInterval.validate_interval_collection(
        ordered_rows,
        step_minutes=INTERVAL_STEP_MINUTES,
    ):
        raise ValueError(
            f'Invalid time intervals for {INTERVAL_DAY_NAMES[day_of_week]}: '
            f'intervals must be ordered, non-overlapping, and use '
            f'{INTERVAL_STEP_MINUTES}-minute increments'
        )

    return ordered_rows


def _build_disabled_interval_placeholder(day_of_week):
    return UserDailyTimeInterval(
        day_of_week=day_of_week,
        sort_order=0,
        start_hour=0,
        start_minute=0,
        end_hour=0,
        end_minute=15,
        is_enabled=False,
    )
