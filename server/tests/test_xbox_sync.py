"""Tests for Xbox cloud sync helpers."""

import asyncio
import json
import pytest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from src.common.xbox_sync import (
    build_xbox_console_stats,
    build_xbox_console_view_context,
    format_xbox_last_sync,
    save_xbox_console_stats,
    get_xbox_console_stats,
    update_xbox_players,
    apply_xbox_playtime,
)


def test_format_xbox_last_sync_datetime():
    dt = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)
    assert format_xbox_last_sync(dt) == '2026-06-13T12:00:00+00:00'


def test_format_xbox_last_sync_seconds():
    # 1740000000 -> 2025-02-19
    assert format_xbox_last_sync(1740000000).startswith('2025-02-19T')


def test_format_xbox_last_sync_milliseconds():
    # 1740000000000 -> 2025-02-19
    assert format_xbox_last_sync(1740000000000).startswith('2025-02-19T')


def test_format_xbox_last_sync_string():
    assert format_xbox_last_sync('2026-06-13T12:00:00+00:00') == '2026-06-13T12:00:00+00:00'


def test_build_xbox_console_stats():
    cloud_device = MagicMock()
    cloud_device.last_seen = 1740000000
    cloud_device.device_name = 'My Xbox'
    cloud_device.device_make = 'Microsoft'
    cloud_device.device_model = 'Xbox Series X'
    cloud_device.os_name = 'Xbox OS'
    cloud_device.today_time_used = 4500000  # 4500 seconds / 75 minutes
    cloud_device.blocked = False

    stats = build_xbox_console_stats(
        cloud_device,
        now_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )

    assert stats['device_name'] == 'My Xbox'
    assert stats['device_make'] == 'Microsoft'
    assert stats['device_model'] == 'Xbox Series X'
    assert stats['today_playing_time'] == 4500000
    assert stats['blocked'] is False


def test_console_stats_round_trip(app, db_session):
    with app.app_context():
        save_xbox_console_stats('xbox-1', {'last_sync': '2026-06-13T12:00:00+00:00'})
        assert get_xbox_console_stats('xbox-1')['last_sync'] == '2026-06-13T12:00:00+00:00'


def test_update_xbox_players():
    db_device = MagicMock()
    account1 = MagicMock()
    account1.user_id = 'user-123'
    account1.first_name = 'John'
    account1.surname = 'Doe'
    account1.profile_picture = 'http://avatar.url'

    update_xbox_players(db_device, [account1])

    parsed = json.loads(db_device.linux_users_json)
    assert len(parsed) == 1
    assert parsed[0]['username'] == 'user-123'
    assert parsed[0]['nickname'] == 'John Doe'
    assert parsed[0]['player_image'] == 'http://avatar.url'


def test_apply_xbox_playtime(app, db_session):
    from src.models import ManagedUser, ManagedUserDeviceMap, UserTimeUsage

    with app.app_context():
        user = ManagedUser(username='child', system_ip='Unassigned', is_valid=True)
        db_session.add(user)
        db_session.commit()

        mapping = ManagedUserDeviceMap(
            managed_user_id=user.id,
            system_id='xbox-1',
            linux_username='user-123',
            last_config='{}',
        )
        db_session.add(mapping)
        db_session.commit()

        today = date(2026, 6, 13)
        apply_xbox_playtime(mapping, player_playtime=3600, today=today)
        db_session.commit()

        usage = UserTimeUsage.query.filter_by(user_id=user.id, date=today).first()
        assert usage is not None
        assert usage.time_spent == 3600


def test_build_xbox_console_view_context(app, db_session):
    from src.models import AgentDevice, ManagedUser, ManagedUserDeviceMap

    with app.app_context():
        device = AgentDevice(system_id='xbox-view', platform='xbox', status='approved')
        user = ManagedUser(username='child', system_ip='Unassigned', is_valid=True)
        db_session.add_all([device, user])
        db_session.commit()

        mapping = ManagedUserDeviceMap(
            managed_user_id=user.id,
            system_id='xbox-view',
            linux_username='user-123',
            last_config=json.dumps({
                'last_sync': '2026-06-13T10:00:00+00:00',
                'TIME_SPENT_DAY': 4500,  # 75 minutes
                'blocked': False,
            }),
        )
        db_session.add(mapping)
        db_session.commit()

        context = build_xbox_console_view_context(device, [mapping])

        assert context['has_data'] is True
        assert context['playtime_summary'] == '1h 15m played today'
        assert context['blocked'] is False
        assert context['is_stale'] is True
        assert context['sync_age_label'] is not None


def test_run_async_with_running_event_loop():
    import asyncio
    from src.common.async_compat import run_async

    async def sample():
        return 'ok'

    async def runner():
        return run_async(sample())

    with pytest.raises(RuntimeError, match='cannot be called from inside a coroutine'):
        asyncio.run(runner())


def test_push_xbox_schedule_changes(app, db_session):
    from unittest.mock import AsyncMock
    from src.models import ManagedUser, ManagedUserDeviceMap, UserWeeklySchedule
    from src.common.xbox_sync import push_xbox_schedule_changes
    from pyfamilysafety.enum import DayOfWeek, OverrideTarget

    with app.app_context():
        user = ManagedUser(username='child', system_ip='Unassigned', is_valid=True)
        db_session.add(user)
        db_session.commit()

        schedule_db = UserWeeklySchedule(
            user_id=user.id,
            monday_hours=2.0,
            tuesday_hours=0.0,
            wednesday_hours=None,
            thursday_hours=1.5,
            friday_hours=3.0,
            saturday_hours=0.0,
            sunday_hours=4.0,
            is_synced=False,
        )
        db_session.add(schedule_db)
        db_session.commit()

        mapping = ManagedUserDeviceMap(
            managed_user_id=user.id,
            system_id='xbox-1',
            linux_username='user-123',
            last_config='{}',
        )
        db_session.add(mapping)
        db_session.commit()

        account = AsyncMock()
        account.user_id = 'user-123'
        account.set_device_limits = AsyncMock()

        today = date(2026, 6, 13)
        now_utc = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)

        asyncio.run(
            push_xbox_schedule_changes(
                account,
                mapping,
                today=today,
                now_utc=now_utc,
            )
        )

        account.set_device_limits.assert_called_once()
        schedule_passed = account.set_device_limits.call_args[0][0]
        assert schedule_passed.platform == OverrideTarget.XBOX

        mon_restriction = schedule_passed.daily_restrictions[DayOfWeek.MONDAY]
        assert mon_restriction.allowance == 7200000
        assert len(mon_restriction.allotted_intervals) == 0

        tue_restriction = schedule_passed.daily_restrictions[DayOfWeek.TUESDAY]
        assert tue_restriction.allowance == 86400000
        assert len(tue_restriction.allotted_intervals) == 1
        assert tue_restriction.allotted_intervals[0].begin == "00:00:00"
        assert tue_restriction.allotted_intervals[0].end == "23:59:00"

        wed_restriction = schedule_passed.daily_restrictions[DayOfWeek.WEDNESDAY]
        assert wed_restriction.allowance == 86400000
        assert len(wed_restriction.allotted_intervals) == 1
        assert wed_restriction.allotted_intervals[0].begin == "00:00:00"
        assert wed_restriction.allotted_intervals[0].end == "23:59:00"

        thu_restriction = schedule_passed.daily_restrictions[DayOfWeek.THURSDAY]
        assert thu_restriction.allowance == 5400000
        assert len(thu_restriction.allotted_intervals) == 0

        assert schedule_db.is_synced is True
        assert schedule_db.last_synced is not None


def test_push_xbox_schedule_changes_no_schedule(app, db_session):
    from unittest.mock import AsyncMock
    from src.models import ManagedUser, ManagedUserDeviceMap
    from src.common.xbox_sync import push_xbox_schedule_changes

    with app.app_context():
        user = ManagedUser(username='child2', system_ip='Unassigned', is_valid=True)
        db_session.add(user)
        db_session.commit()

        mapping = ManagedUserDeviceMap(
            managed_user_id=user.id,
            system_id='xbox-2',
            linux_username='user-456',
            last_config='{}',
        )
        db_session.add(mapping)
        db_session.commit()

        account = AsyncMock()
        account.user_id = 'user-456'
        account.set_device_limits = AsyncMock()

        today = date(2026, 6, 13)
        now_utc = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)

        asyncio.run(
            push_xbox_schedule_changes(
                account,
                mapping,
                today=today,
                now_utc=now_utc,
            )
        )

        account.set_device_limits.assert_not_called()


def test_push_xbox_schedule_changes_already_synced(app, db_session):
    from unittest.mock import AsyncMock
    from src.models import ManagedUser, ManagedUserDeviceMap, UserWeeklySchedule
    from src.common.xbox_sync import push_xbox_schedule_changes

    with app.app_context():
        user = ManagedUser(username='child3', system_ip='Unassigned', is_valid=True)
        db_session.add(user)
        db_session.commit()

        schedule_db = UserWeeklySchedule(
            user_id=user.id,
            monday_hours=2.0,
            is_synced=True,
        )
        db_session.add(schedule_db)
        db_session.commit()

        mapping = ManagedUserDeviceMap(
            managed_user_id=user.id,
            system_id='xbox-3',
            linux_username='user-789',
            last_config='{}',
        )
        db_session.add(mapping)
        db_session.commit()

        account = AsyncMock()
        account.user_id = 'user-789'
        account.set_device_limits = AsyncMock()

        today = date(2026, 6, 13)
        now_utc = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)

        asyncio.run(
            push_xbox_schedule_changes(
                account,
                mapping,
                today=today,
                now_utc=now_utc,
            )
        )

        account.set_device_limits.assert_not_called()
