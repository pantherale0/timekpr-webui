"""Shared helpers for Xbox Parental Controls cloud sync using pyfamilysafety."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from datetime import date, datetime, time as dt_time, timezone

from src.models import (
    AgentDevice,
    ManagedUserDeviceMap,
    Settings,
    UserTimeUsage,
    db,
    stamp_usage_snapshot,
)
from pyfamilysafety.enum import OverrideTarget, DayOfWeek
from pyfamilysafety.schedule import DeviceLimitsSchedule, DailyRestriction, AllottedInterval

_LOGGER = logging.getLogger(__name__)

XBOX_CONSOLE_STATS_PREFIX = 'xbox_console_stats_'


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


def xbox_console_stats_key(system_id: str) -> str:
    return f'{XBOX_CONSOLE_STATS_PREFIX}{system_id}'


def format_xbox_last_sync(raw) -> str | None:
    """Normalize Xbox last-sync timestamps for storage/display."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=timezone.utc).isoformat()
    if isinstance(raw, (int, float)):
        timestamp = float(raw)
        if timestamp > 1e12:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    if isinstance(raw, str):
        return raw
    return str(raw)


def build_xbox_console_stats(cloud_device, *, now_utc: datetime) -> dict:
    """Extract Xbox device stats for storing in settings."""
    return {
        'last_sync': format_xbox_last_sync(cloud_device.last_seen),
        'device_name': cloud_device.device_name,
        'device_make': cloud_device.device_make,
        'device_model': cloud_device.device_model,
        'os_name': cloud_device.os_name,
        'today_playing_time': cloud_device.today_time_used, # in milliseconds or seconds
        'blocked': cloud_device.blocked,
        'synced_at': now_utc.isoformat(),
    }


def save_xbox_console_stats(system_id: str, stats: dict) -> None:
    Settings.set_value(xbox_console_stats_key(system_id), json.dumps(stats))


def get_xbox_console_stats(system_id: str) -> dict:
    raw = Settings.get_value(xbox_console_stats_key(system_id))
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_xbox_mapping_stats(
    cloud_device,
    *,
    player_playtime: int,
    last_active_str: str,
    now_utc: datetime,
) -> dict:
    """Extract Xbox stats for storing in mapping config."""
    return stamp_usage_snapshot({
        'TIME_SPENT_DAY': player_playtime,
        'TIME_LEFT_DAY': None, # Read-only for now
        'LIMIT_TIME': None,    # Read-only for now
        'last_playtime_change_at': last_active_str,
        'last_sync': format_xbox_last_sync(cloud_device.last_seen),
        'blocked': cloud_device.blocked,
    }, now_utc.date())


def update_xbox_players(db_device: AgentDevice, accounts: list) -> None:
    """Update system users json list for Xbox consoles based on the family accounts roster."""
    players_list = []
    for account in accounts:
        players_list.append({
            'username': account.user_id,
            'uid': 0,
            'nickname': f"{account.first_name} {account.surname or ''}".strip(),
            'player_image': account.profile_picture,
        })
    db_device.linux_users_json = json.dumps(players_list)


def apply_xbox_playtime(
    mapping: ManagedUserDeviceMap,
    *,
    player_playtime: int,
    today: date,
) -> None:
    """Apply synced Xbox playtime to database."""
    user = mapping.managed_user
    if not user:
        return
    usage = UserTimeUsage.query.filter_by(user_id=user.id, date=today).first()
    if usage:
        usage.time_spent = player_playtime
    else:
        db.session.add(UserTimeUsage(user_id=user.id, date=today, time_spent=player_playtime))


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


def build_xbox_console_view_context(device: AgentDevice, mapped_accounts) -> dict | None:
    """Build template-friendly Xbox console settings for the device detail page."""
    if (device.platform or '').strip().lower() != 'xbox':
        return None

    first_mapping = mapped_accounts[0] if mapped_accounts else None
    console_stats = device.xbox_console_stats or {}

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

    played_minutes = None
    mapping_played = first_mapping.get_config_value('TIME_SPENT_DAY') if first_mapping else None
    if mapping_played is not None:
        try:
            played_minutes = max(int(float(mapping_played) // 60), 0)
        except (TypeError, ValueError):
            played_minutes = None
    elif console_stats.get('today_playing_time') is not None:
        try:
            raw_time = float(console_stats.get('today_playing_time', 0))
            # Dynamic detection: if > 10,000 assume milliseconds, otherwise seconds
            if raw_time > 10000:
                played_minutes = max(int(raw_time // 60000), 0)
            else:
                played_minutes = max(int(raw_time // 60), 0)
        except (TypeError, ValueError):
            played_minutes = None

    playtime_summary = None
    if played_minutes is not None:
        playtime_summary = f'{_format_minutes_short(played_minutes)} played today'

    blocked = pick('blocked', 'blocked')
    has_data = bool(last_sync_dt or playtime_summary)

    return {
        'last_sync_dt': last_sync_dt,
        'is_stale': is_stale,
        'sync_age_label': sync_age_label,
        'played_minutes': played_minutes,
        'playtime_summary': playtime_summary,
        'blocked': blocked,
        'has_data': has_data,
        'device_name': console_stats.get('device_name') or device.system_hostname,
        'device_make': console_stats.get('device_make'),
        'device_model': console_stats.get('device_model'),
        'os_name': console_stats.get('os_name'),
    }


async def push_xbox_schedule_changes(
    account,
    mapping: ManagedUserDeviceMap,
    *,
    today: date,
    now_utc: datetime,
) -> None:
    """Push daily playtime limit changes to Microsoft Family Safety for Xbox."""
    user = mapping.managed_user
    if not user:
        return

    if user.weekly_schedule and not user.weekly_schedule.is_synced:
        daily_restrictions = {}
        day_map = {
            1: DayOfWeek.MONDAY,
            2: DayOfWeek.TUESDAY,
            3: DayOfWeek.WEDNESDAY,
            4: DayOfWeek.THURSDAY,
            5: DayOfWeek.FRIDAY,
            6: DayOfWeek.SATURDAY,
            7: DayOfWeek.SUNDAY,
        }

        day_names = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

        for day_num, day_enum in day_map.items():
            day_name = day_names[day_num - 1]
            limit_hours = getattr(user.weekly_schedule, f"{day_name}_hours", 0)

            # Unlimited: set allowed range to 00:00 to 23:59 and 24h allowance
            if limit_hours is None or limit_hours <= 0:
                allowance_ms = 24 * 60 * 60 * 1000
                allotted = [AllottedInterval("00:00:00", "23:59:00")]
            else:
                allowance_ms = int(limit_hours * 3600 * 1000)
                allotted = []

            daily_restrictions[day_enum] = DailyRestriction(
                allowance=allowance_ms,
                allotted_intervals=allotted
            )

        schedule = DeviceLimitsSchedule(
            platform=OverrideTarget.XBOX,
            daily_restrictions=daily_restrictions
        )

        _LOGGER.info("Pushing Xbox daily limit schedule changes for user %s", user.username)
        await account.set_device_limits(schedule)
        user.weekly_schedule.mark_synced()

