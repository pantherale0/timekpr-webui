"""Tests for persisted pending agent commands."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.agent_helper import AgentConnectionManager
from src.database import (
    AgentDevice,
    ManagedUser,
    ManagedUserDeviceMap,
    PendingCommand,
    UserDailyTimeInterval,
    UserWeeklySchedule,
)
from src.pending_commands_manager import (
    DOMAIN_RECONCILE_ACTION,
    enqueue_command,
    enqueue_domain_reconcile,
    enqueue_policy_snapshot,
    expire_stale_commands,
    flush_pending_commands,
    get_pending_count,
    queue_offline_command,
    rebuild_command_args,
)


class DummyWS:
    def __init__(self):
        self.sent_messages = []

    def send(self, message):
        self.sent_messages.append(message)


@pytest.fixture
def approved_device(db_session):
    device = AgentDevice(
        system_id='pending-cmd-device',
        system_hostname='family-pc',
        status='approved',
        secure_token='secure-token',
        platform='linux',
    )
    db_session.add(device)
    db_session.commit()
    return device


def test_enqueue_command_when_offline_via_send_command_sync(approved_device, db_session):
    success, message, data = AgentConnectionManager.send_command_sync(
        approved_device.system_id,
        'capture_screenshot',
        'child',
        {'linux_username': 'child'},
    )

    assert success is True
    assert 'queued' in message.lower()
    assert data == {'queued': True}
    assert get_pending_count(approved_device.system_id) == 1


def test_non_queueable_action_still_fails_offline(approved_device):
    success, message, _data = AgentConnectionManager.send_command_sync(
        approved_device.system_id,
        'validate_user',
        'child',
    )

    assert success is False
    assert 'offline' in message.lower()


def test_policy_snapshot_coalesces_to_latest_marker(approved_device, db_session):
    enqueue_policy_snapshot(approved_device.system_id, 'sync_linux_device_policy', 'child')
    enqueue_policy_snapshot(approved_device.system_id, 'sync_linux_device_policy', 'child')
    enqueue_policy_snapshot(approved_device.system_id, 'sync_linux_device_policy', 'child')

    pending = PendingCommand.query.filter_by(
        system_id=approved_device.system_id,
        status=PendingCommand.STATUS_PENDING,
    ).all()
    superseded = PendingCommand.query.filter_by(
        system_id=approved_device.system_id,
        status=PendingCommand.STATUS_SUPERSEDED,
    ).count()

    assert len(pending) == 1
    assert superseded == 2


def test_factory_reset_supersedes_older_pending_wipe(approved_device, db_session):
    enqueue_command(approved_device.system_id, 'factory_reset', username='child')
    enqueue_command(approved_device.system_id, 'factory_reset', username='child')

    pending = PendingCommand.query.filter_by(
        system_id=approved_device.system_id,
        action='factory_reset',
        status=PendingCommand.STATUS_PENDING,
    ).count()
    assert pending == 1


def test_domain_reconcile_supersedes_duplicates(approved_device, db_session):
    enqueue_domain_reconcile(approved_device.system_id)
    enqueue_domain_reconcile(approved_device.system_id)

    pending = PendingCommand.query.filter_by(
        system_id=approved_device.system_id,
        action=DOMAIN_RECONCILE_ACTION,
        status=PendingCommand.STATUS_PENDING,
    ).count()
    assert pending == 1


def test_flush_delivers_imperative_commands_fifo(approved_device, db_session):
    enqueue_command(approved_device.system_id, 'refresh_installed_apps', username='child')
    enqueue_command(approved_device.system_id, 'capture_screenshot', username='child', args={})

    ws = DummyWS()
    AgentConnectionManager.register(approved_device.system_id, ws, '127.0.0.1')

    with patch.object(
        AgentConnectionManager,
        'send_command_sync',
        side_effect=[
            (True, 'ok', {'queued': True}),
            (True, 'ok', {'queued': True}),
        ],
    ) as mock_send:
        try:
            result = flush_pending_commands(approved_device.system_id)
        finally:
            AgentConnectionManager.unregister(approved_device.system_id)

    assert result.delivered == 2
    assert mock_send.call_count == 2
    assert get_pending_count(approved_device.system_id) == 0


def test_expire_stale_commands_marks_expired_rows(approved_device, db_session):
    row = enqueue_command(
        approved_device.system_id,
        'capture_screenshot',
        username='child',
        args={},
    )
    row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    expired = expire_stale_commands()

    assert expired == 1
    refreshed = PendingCommand.query.get(row.id)
    assert refreshed.status == PendingCommand.STATUS_EXPIRED


def test_flush_aborts_when_agent_disconnects_mid_queue(approved_device, db_session):
    enqueue_command(approved_device.system_id, 'refresh_installed_apps', username='child')
    enqueue_command(approved_device.system_id, 'capture_screenshot', username='child', args={})

    ws = DummyWS()
    AgentConnectionManager.register(approved_device.system_id, ws, '127.0.0.1')

    def _send_and_disconnect(*_args, **_kwargs):
        AgentConnectionManager.unregister(approved_device.system_id)
        return False, 'Agent offline', None

    with patch.object(AgentConnectionManager, 'send_command_sync', side_effect=_send_and_disconnect):
        try:
            result = flush_pending_commands(approved_device.system_id)
        finally:
            AgentConnectionManager.unregister(approved_device.system_id)

    assert result.skipped_offline == 1
    assert get_pending_count(approved_device.system_id) == 2


def test_queue_if_offline_false_fails_immediately(approved_device):
    AgentConnectionManager.unregister(approved_device.system_id)
    success, message, _data = AgentConnectionManager.send_command_sync(
        approved_device.system_id,
        'capture_screenshot',
        'child',
        queue_if_offline=False,
    )

    assert success is False
    assert 'offline' in message.lower()
    assert get_pending_count(approved_device.system_id) == 0


def test_queue_offline_command_routes_policy_snapshot(approved_device, db_session):
    row = queue_offline_command(
        approved_device.system_id,
        'sync_screenshot_policy',
        '',
    )

    assert row.command_kind == PendingCommand.KIND_POLICY_SNAPSHOT
    assert row.args_json is None


def test_rebuild_command_args_set_weekly_time_limits(approved_device, db_session):
    user = ManagedUser(username='caiden', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush()
    db_session.add(UserWeeklySchedule(user_id=user.id, monday_hours=2.0, tuesday_hours=1.5))
    db_session.add(ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=approved_device.system_id,
        linux_username='caiden',
        is_valid=True,
    ))
    db_session.commit()

    row = enqueue_policy_snapshot(
        approved_device.system_id,
        'set_weekly_time_limits',
        'caiden',
    )

    args = rebuild_command_args(row)

    assert args is not None
    assert args['schedule']['monday'] == 2.0
    assert args['schedule']['tuesday'] == 1.5


def test_rebuild_command_args_set_allowed_hours(approved_device, db_session):
    user = ManagedUser(username='caiden', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush()
    db_session.add(UserDailyTimeInterval(
        user_id=user.id,
        day_of_week=1,
        start_hour=9,
        start_minute=0,
        end_hour=17,
        end_minute=0,
    ))
    db_session.add(ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=approved_device.system_id,
        linux_username='caiden',
        is_valid=True,
    ))
    db_session.commit()

    row = enqueue_policy_snapshot(
        approved_device.system_id,
        'set_allowed_hours',
        'caiden',
    )

    args = rebuild_command_args(row)

    assert args is not None
    assert 'intervals' in args
    monday = args['intervals']['1']
    for hour in ('9', '10', '11', '12', '13', '14', '15', '16'):
        assert monday[hour] == {'STARTMIN': 0, 'ENDMIN': 60, 'UACC': 0}


def test_flush_set_weekly_time_limits_rebuilds_from_managed_user(approved_device, db_session):
    user = ManagedUser(username='caiden', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush()
    schedule = UserWeeklySchedule(user_id=user.id, monday_hours=3.0, is_synced=False)
    db_session.add(schedule)
    db_session.add(ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=approved_device.system_id,
        linux_username='caiden',
        is_valid=True,
    ))
    db_session.commit()

    enqueue_policy_snapshot(
        approved_device.system_id,
        'set_weekly_time_limits',
        'caiden',
    )

    ws = DummyWS()
    AgentConnectionManager.register(approved_device.system_id, ws, '127.0.0.1')

    with patch.object(
        AgentConnectionManager,
        'send_command_sync',
        return_value=(True, 'ok', None),
    ) as mock_send:
        try:
            result = flush_pending_commands(approved_device.system_id)
        finally:
            AgentConnectionManager.unregister(approved_device.system_id)

    assert result.delivered == 1
    mock_send.assert_called_once()
    call_args = mock_send.call_args[0]
    assert call_args[0] == approved_device.system_id
    assert call_args[1] == 'set_weekly_time_limits'
    assert call_args[2] == 'caiden'
    assert call_args[3]['schedule']['monday'] == 3.0
    db_session.refresh(schedule)
    assert schedule.is_synced is True
