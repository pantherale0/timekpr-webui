"""Tests for Nintendo cloud sync helpers."""

import asyncio
from datetime import datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock

from pynintendoparental.enum import DeviceTimerMode

from src.common.nintendo_sync import (
    build_nintendo_console_stats,
    build_nintendo_console_view_context,
    format_nintendo_last_sync,
    push_nintendo_schedule_changes,
    resolve_target_bedtime,
    save_nintendo_console_stats,
    get_nintendo_console_stats,
)


def test_format_nintendo_last_sync_seconds():
    assert format_nintendo_last_sync(1740000000).startswith('2025-02-19T')


def test_format_nintendo_last_sync_milliseconds():
    assert format_nintendo_last_sync(1740000000000).startswith('2025-02-19T')


def test_build_nintendo_console_stats():
    cloud_device = MagicMock()
    cloud_device.last_sync = 1740000000
    cloud_device.timer_mode = DeviceTimerMode.DAILY
    cloud_device.bedtime_alarm = time(21, 0)
    cloud_device.limit_time = 120
    cloud_device.today_playing_time = 45

    stats = build_nintendo_console_stats(
        cloud_device,
        now_utc=datetime(2025, 2, 20, tzinfo=timezone.utc),
    )

    assert stats['timer_mode'] == 'DAILY'
    assert stats['bedtime_alarm'] == '21:00:00'
    assert stats['limit_time'] == 120


def test_console_stats_round_trip(app, db_session):
    with app.app_context():
        save_nintendo_console_stats('switch-1', {'last_sync': '2025-02-20T12:00:00+00:00'})
        assert get_nintendo_console_stats('switch-1')['last_sync'] == '2025-02-20T12:00:00+00:00'


def test_resolve_target_bedtime_without_intervals(app, db_session):
    user = MagicMock()
    user.id = 999
    with app.app_context():
        assert resolve_target_bedtime(user, day_of_week=1) is None


def test_push_nintendo_schedule_skips_bedtime_when_unconfigured(app, db_session):
    cloud_device = AsyncMock()
    cloud_device.bedtime_alarm = time(21, 0)
    cloud_device.limit_time = 120

    mapping = MagicMock()
    mapping.managed_user = MagicMock()
    mapping.managed_user.id = 999
    mapping.managed_user.weekly_schedule = None

    with app.app_context():
        asyncio.run(
            push_nintendo_schedule_changes(
                cloud_device,
                mapping,
                today=datetime.now(timezone.utc).date(),
                now_utc=datetime.now(timezone.utc),
            )
        )

    cloud_device.set_bedtime_alarm.assert_not_called()


def test_run_async_with_running_event_loop():
    import asyncio
    from src.common.nintendo_sync import run_async

    async def sample():
        return 'ok'

    async def runner():
        return run_async(sample())

    assert asyncio.run(runner()) == 'ok'


def test_build_nintendo_console_view_context(app, db_session):
    from src.models import AgentDevice, ManagedUser, ManagedUserDeviceMap
    import json

    device = AgentDevice(system_id='switch-ui', platform='nintendo', status='approved')
    user = ManagedUser(username='child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.commit()

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='switch-ui',
        linux_username='player-1',
        last_config=json.dumps({
            'last_sync': '2026-06-06T22:06:27+00:00',
            'timer_mode': 'DAILY',
            'bedtime_alarm': '19:00:00',
            'LIMIT_TIME': 120,
            'TIME_SPENT_DAY': 45 * 60,
        }),
    )
    db_session.add(mapping)
    db_session.commit()

    with app.app_context():
        context = build_nintendo_console_view_context(device, [mapping])

    assert context['has_data'] is True
    assert context['timer_mode'] == 'Daily limit'
    assert context['bedtime'] == '19:00'
    assert context['playtime_summary'] == '45m of 2h'
    assert context['progress_pct'] == 38
    assert context['is_stale'] is True
    assert context['sync_age_label'] is not None
