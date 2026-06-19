"""Shared helpers for Nintendo Parental Controls cloud sync."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from datetime import date, datetime, time as dt_time, timezone

from src.database import (
    AgentDevice,
    ManagedUserDeviceMap,
    Settings,
    UserDailyTimeInterval,
    UserTimeUsage,
    db,
    stamp_usage_snapshot,
)

_LOGGER = logging.getLogger(__name__)

NINTENDO_CONSOLE_STATS_PREFIX = 'nintendo_console_stats_'


def run_async(coro):
    """Run a coroutine from synchronous code.

    Works when no event loop is running, and when the caller is already inside
    one (e.g. gevent-monkeypatched asyncio under gunicorn).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def nintendo_console_stats_key(system_id: str) -> str:
    return f'{NINTENDO_CONSOLE_STATS_PREFIX}{system_id}'


def format_nintendo_last_sync(raw) -> str | None:
    """Normalize Nintendo last-sync timestamps for storage/display."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        timestamp = float(raw)
        if timestamp > 1e12:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    if isinstance(raw, str):
        return raw
    return str(raw)


def build_nintendo_console_stats(cloud_device, *, now_utc: datetime) -> dict:
    timer_mode = cloud_device.timer_mode
    return {
        'last_sync': format_nintendo_last_sync(cloud_device.last_sync),
        'timer_mode': timer_mode.name if timer_mode is not None else None,
        'bedtime_alarm': str(cloud_device.bedtime_alarm) if cloud_device.bedtime_alarm else None,
        'limit_time': cloud_device.limit_time,
        'today_playing_time': cloud_device.today_playing_time,
        'synced_at': now_utc.isoformat(),
    }


def save_nintendo_console_stats(system_id: str, stats: dict) -> None:
    Settings.set_value(nintendo_console_stats_key(system_id), json.dumps(stats))


def get_nintendo_console_stats(system_id: str) -> dict:
    raw = Settings.get_value(nintendo_console_stats_key(system_id))
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_nintendo_mapping_stats(
    cloud_device,
    *,
    player_playtime: int,
    global_playtime_seconds: int,
    last_active_str: str,
    now_utc: datetime,
) -> dict:
    timer_mode = cloud_device.timer_mode
    return stamp_usage_snapshot({
        'TIME_SPENT_DAY': player_playtime,
        'TIME_LEFT_DAY': (
            max((cloud_device.limit_time * 60) - global_playtime_seconds, 0)
            if cloud_device.limit_time != -1
            else None
        ),
        'LIMIT_TIME': cloud_device.limit_time,
        'timer_mode': timer_mode.name if timer_mode is not None else None,
        'bedtime_alarm': str(cloud_device.bedtime_alarm) if cloud_device.bedtime_alarm else None,
        'last_playtime_change_at': last_active_str,
        'last_sync': format_nintendo_last_sync(cloud_device.last_sync),
    }, now_utc.date())


def update_nintendo_players(db_device: AgentDevice, cloud_device) -> None:
    players_list = []
    for player in cloud_device.players.values():
        players_list.append({
            'username': player.player_id,
            'uid': 0,
            'nickname': player.nickname,
            'player_image': player.player_image,
        })
    db_device.linux_users_json = json.dumps(players_list)


def apply_nintendo_playtime(
    mapping: ManagedUserDeviceMap,
    *,
    player_playtime: int,
    today: date,
) -> None:
    user = mapping.managed_user
    if not user:
        return
    usage = UserTimeUsage.query.filter_by(user_id=user.id, date=today).first()
    if usage:
        usage.time_spent = player_playtime
    else:
        db.session.add(UserTimeUsage(user_id=user.id, date=today, time_spent=player_playtime))


def resolve_target_bedtime(user, *, day_of_week: int) -> dt_time | None:
    intervals = UserDailyTimeInterval.query.filter_by(
        user_id=user.id,
        day_of_week=day_of_week,
        is_enabled=True,
    ).all()
    if not intervals:
        return None
    sorted_intervals = sorted(intervals, key=lambda item: (item.end_hour, item.end_minute))
    last_interval = sorted_intervals[-1]
    if 0 < last_interval.end_hour < 24:
        return dt_time(last_interval.end_hour, last_interval.end_minute)
    return None


async def push_nintendo_schedule_changes(
    cloud_device,
    mapping: ManagedUserDeviceMap,
    *,
    today: date,
    now_utc: datetime,
) -> None:
    """Push Guardian schedules to Nintendo. Failures are logged by the caller."""
    user = mapping.managed_user
    if not user:
        return

    if user.weekly_schedule and not user.weekly_schedule.is_synced:
        limit_seconds = user.weekly_schedule.get_limit_seconds_for_day(today)
        if limit_seconds is not None:
            limit_minutes = max(limit_seconds // 60, 0)
            if cloud_device.limit_time != limit_minutes:
                await cloud_device.update_max_daily_playtime(limit_minutes)
        user.weekly_schedule.mark_synced()

    target_bedtime = resolve_target_bedtime(user, day_of_week=now_utc.isoweekday())
    if target_bedtime is None:
        return

    current_bedtime = cloud_device.bedtime_alarm
    if current_bedtime != target_bedtime:
        await cloud_device.set_bedtime_alarm(target_bedtime)


def _parse_iso_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _format_bedtime(value) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = dt_time.fromisoformat(text)
        return parsed.strftime('%H:%M')
    except ValueError:
        return text[:5] if len(text) >= 5 else text


def _format_timer_mode(value) -> str | None:
    if not value:
        return None
    normalized = str(value).strip().upper()
    labels = {
        'DAILY': 'Daily limit',
        'EACH_DAY_OF_THE_WEEK': 'Per day of week',
    }
    return labels.get(normalized, str(value).replace('_', ' ').title())


def _format_minutes_short(minutes: int) -> str:
    minutes = max(int(minutes), 0)
    if minutes >= 60:
        hours = minutes // 60
        remainder = minutes % 60
        if remainder:
            return f'{hours}h {remainder}m'
        return f'{hours}h'
    return f'{minutes}m'


def _format_relative_age(sync_dt: datetime, now_utc: datetime) -> str:
    delta = now_utc - sync_dt.astimezone(timezone.utc)
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return 'just now'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes} minute{"s" if minutes != 1 else ""} ago'
    hours = minutes // 60
    if hours < 24:
        return f'{hours} hour{"s" if hours != 1 else ""} ago'
    days = hours // 24
    return f'{days} day{"s" if days != 1 else ""} ago'


def build_nintendo_console_view_context(device: AgentDevice, mapped_accounts) -> dict | None:
    """Build template-friendly Nintendo console settings for the device detail page."""
    if (device.platform or '').strip().lower() != 'nintendo':
        return None

    first_mapping = mapped_accounts[0] if mapped_accounts else None
    console_stats = device.nintendo_console_stats or {}

    def pick(mapping_key, stats_key):
        if first_mapping:
            value = first_mapping.get_config_value(mapping_key)
            if value is not None:
                return value
        return console_stats.get(stats_key)

    last_sync_raw = pick('last_sync', 'last_sync')
    last_sync_dt = _parse_iso_datetime(last_sync_raw)
    now_utc = datetime.now(timezone.utc)
    is_stale = True
    sync_age_label = None
    if last_sync_dt:
        age_seconds = (now_utc - last_sync_dt.astimezone(timezone.utc)).total_seconds()
        is_stale = age_seconds > 1800
        sync_age_label = _format_relative_age(last_sync_dt, now_utc)

    limit_raw = pick('LIMIT_TIME', 'limit_time')
    try:
        limit_minutes = int(limit_raw) if limit_raw is not None else None
    except (TypeError, ValueError):
        limit_minutes = None

    played_minutes = None
    mapping_played = first_mapping.get_config_value('TIME_SPENT_DAY') if first_mapping else None
    if mapping_played is not None:
        try:
            played_minutes = max(int(float(mapping_played) // 60), 0)
        except (TypeError, ValueError):
            played_minutes = None
    elif console_stats.get('today_playing_time') is not None:
        try:
            played_minutes = max(int(console_stats.get('today_playing_time')), 0)
        except (TypeError, ValueError):
            played_minutes = None

    has_limit = limit_minutes is not None
    unlimited = has_limit and limit_minutes == -1
    progress_pct = None
    playtime_summary = None
    if played_minutes is not None and has_limit and not unlimited and limit_minutes > 0:
        progress_pct = min(100, round((played_minutes / limit_minutes) * 100))
        playtime_summary = f'{_format_minutes_short(played_minutes)} of {_format_minutes_short(limit_minutes)}'
    elif played_minutes is not None and unlimited:
        playtime_summary = f'{_format_minutes_short(played_minutes)} played today'
    elif played_minutes is not None:
        playtime_summary = f'{_format_minutes_short(played_minutes)} played today'

    timer_mode = _format_timer_mode(pick('timer_mode', 'timer_mode'))
    bedtime = _format_bedtime(pick('bedtime_alarm', 'bedtime_alarm'))
    has_data = bool(last_sync_dt or timer_mode or bedtime or playtime_summary)

    return {
        'last_sync_dt': last_sync_dt,
        'is_stale': is_stale,
        'sync_age_label': sync_age_label,
        'timer_mode': timer_mode,
        'bedtime': bedtime,
        'limit_minutes': None if unlimited else limit_minutes,
        'played_minutes': played_minutes,
        'progress_pct': progress_pct,
        'playtime_summary': playtime_summary,
        'unlimited': unlimited,
        'has_data': has_data,
    }
