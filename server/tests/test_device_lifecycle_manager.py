"""Unit tests for device_lifecycle_manager."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.database import AgentDevice, ManagedUser, ManagedUserDeviceMap
from src.device_lifecycle_manager import (
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


@patch('src.device_lifecycle_manager.AgentConnectionManager.is_online', return_value=False)
@patch('src.device_lifecycle_manager.device_prefers_push', return_value=True)
@patch('src.device_lifecycle_manager.AgentClient.factory_reset_device', return_value=(False, 'offline'))
def test_factory_reset_sets_pending_when_offline(
    mock_factory_reset,
    mock_push,
    mock_online,
    approved_android_device,
    db_session,
):
    result = unenroll_device('sys-android-life', MODE_FACTORY_RESET)

    assert result['success'] is True
    assert result['pending_factory_reset'] is True
    assert result['server_revoked'] is True
    mock_factory_reset.assert_called_once()

    refreshed = AgentDevice.query.get('sys-android-life')
    assert refreshed.status == 'rejected'
    assert refreshed.pending_factory_reset is True
    assert refreshed.secure_token == 'secure-token'
    assert refreshed.unenrolled_at is not None


@patch('src.device_lifecycle_manager.AgentConnectionManager.is_online', return_value=True)
@patch('src.device_lifecycle_manager.AgentClient.unenroll_device', return_value=(True, 'cleared'))
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


@patch('src.device_lifecycle_manager.AgentClient.factory_reset_device', return_value=(True, 'wiping'))
def test_deliver_pending_factory_reset_clears_flag(mock_factory_reset, approved_android_device, db_session):
    approved_android_device.pending_factory_reset = True
    approved_android_device.status = 'rejected'
    db_session.commit()

    delivered = deliver_pending_factory_reset_on_connect('sys-android-life')

    assert delivered is True
    mock_factory_reset.assert_called_once_with('child')
    refreshed = AgentDevice.query.get('sys-android-life')
    assert refreshed.pending_factory_reset is False
    assert refreshed.secure_token is None
