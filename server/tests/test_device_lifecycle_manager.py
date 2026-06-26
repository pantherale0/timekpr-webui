"""Unit tests for device_lifecycle_manager."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.models import AgentDevice, ManagedUser, ManagedUserDeviceMap, PendingCommand
from src.device.lifecycle import (
    MODE_FACTORY_RESET,
    MODE_UNENROLL,
    deliver_pending_factory_reset_on_connect,
    unenroll_device,
)


@pytest.fixture
def approved_linux_device(db_session):
    device = AgentDevice(
        system_id='sys-linux-life',
        system_hostname='family-pc',
        status='approved',
        secure_token='secure-token',
        platform='linux',
    )
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def approved_android_device(db_session):
    device = AgentDevice(
        system_id='sys-android-life',
        system_hostname='kid-phone',
        status='approved',
        secure_token='secure-token',
        platform='android',
        fcm_token='fcm-token-123',
    )
    user = ManagedUser(username='child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-android-life',
        linux_username='child',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()
    return device


def test_unenroll_rejects_already_unenrolled(approved_linux_device):
    approved_linux_device.status = 'rejected'
    approved_linux_device.secure_token = None

    result = unenroll_device('sys-linux-life', MODE_UNENROLL)

    assert result['success'] is False
    assert result['status_code'] == 400


def test_factory_reset_blocked_on_linux(approved_linux_device):
    result = unenroll_device('sys-linux-life', MODE_FACTORY_RESET)

    assert result['success'] is False
    assert 'Android' in result['message']
    assert result['status_code'] == 400


@patch('src.device.lifecycle.AgentConnectionManager.is_online', return_value=False)
@patch('src.device.lifecycle.device_prefers_push', return_value=True)
@patch('src.device.lifecycle.AgentClient.factory_reset_device', return_value=(False, 'offline'))
def test_factory_reset_queues_command_when_push_delivery_fails(
    mock_factory_reset,
    mock_push,
    mock_online,
    approved_android_device,
    db_session,
):
    result = unenroll_device('sys-android-life', MODE_FACTORY_RESET)

    assert result['success'] is True
    assert result['queued'] is True
    assert result['pending_factory_reset'] is True
    assert result['status_code'] == 202
    mock_factory_reset.assert_called_once()

    refreshed = AgentDevice.query.get('sys-android-life')
    assert refreshed.status == 'rejected'
    assert refreshed.pending_factory_reset is False
    assert refreshed.secure_token == 'secure-token'

    pending = PendingCommand.query.filter_by(
        system_id='sys-android-life',
        action='factory_reset',
        status=PendingCommand.STATUS_PENDING,
    ).count()
    assert pending == 1


@patch('src.device.lifecycle.AgentConnectionManager.is_online', return_value=False)
@patch('src.device.lifecycle.device_prefers_push', return_value=False)
def test_offline_unenroll_queues_agent_cleanup(
    mock_push,
    mock_online,
    approved_linux_device,
    db_session,
):
    result = unenroll_device('sys-linux-life', MODE_UNENROLL)

    assert result['success'] is True
    assert result['queued'] is True
    assert result['status_code'] == 202

    pending = PendingCommand.query.filter_by(
        system_id='sys-linux-life',
        action='unenroll',
        status=PendingCommand.STATUS_PENDING,
    ).count()
    assert pending == 1


@patch('src.device.lifecycle.AgentConnectionManager.is_online', return_value=True)
@patch('src.device.lifecycle.AgentClient.unenroll_device', return_value=(True, 'cleared'))
def test_unenroll_revokes_and_records_timestamp(
    mock_unenroll,
    mock_online,
    approved_linux_device,
    db_session,
):
    result = unenroll_device('sys-linux-life', MODE_UNENROLL)

    assert result['success'] is True
    assert result['delivered_to_agent'] is True
    assert result['server_revoked'] is True
    mock_unenroll.assert_called_once()

    refreshed = AgentDevice.query.get('sys-linux-life')
    assert refreshed.status == 'rejected'
    assert refreshed.secure_token is None
    assert refreshed.unenrolled_at is not None


@patch('src.agent.pending_commands.flush_pending_commands')
def test_deliver_pending_factory_reset_uses_queue(
    mock_flush,
    approved_android_device,
    db_session,
):
    from src.agent.pending_commands import FlushResult

    approved_android_device.pending_factory_reset = True
    approved_android_device.status = 'rejected'
    db_session.commit()
    mock_flush.return_value = FlushResult(delivered=1)

    delivered = deliver_pending_factory_reset_on_connect('sys-android-life')

    assert delivered is True
    mock_flush.assert_called_once_with('sys-android-life')
    refreshed = AgentDevice.query.get('sys-android-life')
    assert refreshed.pending_factory_reset is False
    assert refreshed.secure_token is None

    pending = PendingCommand.query.filter_by(
        system_id='sys-android-life',
        action='factory_reset',
    ).count()
    assert pending == 1
