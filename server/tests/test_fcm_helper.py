"""Tests for FCM and agent push routing."""

from unittest.mock import MagicMock, patch

import pytest

from src.agent_push import (
    android_push_wake_available,
    android_should_use_persistent_websocket,
    device_prefers_push,
    notify_device_message,
    update_device_push_metadata,
)
from src.database import AgentDevice
from src.fcm_helper import is_fcm_configured, send_data_message


def test_is_fcm_configured_env(monkeypatch):
    monkeypatch.delenv('FCM_SERVER_KEY', raising=False)
    monkeypatch.delenv('FIREBASE_CREDENTIALS_JSON', raising=False)
    assert is_fcm_configured() is False

    monkeypatch.setenv('FCM_SERVER_KEY', 'test-key')
    assert is_fcm_configured() is True


@patch('src.fcm_helper.requests.post')
def test_send_data_message_legacy(mock_post, monkeypatch):
    monkeypatch.setenv('FCM_SERVER_KEY', 'legacy-key')
    monkeypatch.delenv('FIREBASE_CREDENTIALS_JSON', raising=False)
    mock_post.return_value = MagicMock(status_code=200, json=lambda: {'success': 1, 'failure': 0})

    ok, message = send_data_message('device-token', {'action': 'sync_policies'})
    assert ok is True
    assert 'accepted' in message.lower()
    mock_post.assert_called_once()


def test_update_device_push_metadata(db_session):
    device = AgentDevice(system_id='android-1', status='pending')
    db_session.add(device)
    db_session.commit()

    update_device_push_metadata(
        device,
        {
            'platform': 'android',
            'fcm_token': 'abc123',
        },
    )
    assert device.platform == 'android'
    assert device.fcm_token == 'abc123'
    assert device.fcm_token_updated_at is not None


def test_device_prefers_push(db_session):
    device = AgentDevice(
        system_id='android-2',
        status='approved',
        platform='android',
        fcm_token='token',
    )
    assert device_prefers_push(device) is True

    linux_device = AgentDevice(system_id='linux-1', status='approved', platform='linux')
    assert device_prefers_push(linux_device) is False


def test_android_push_wake_available(monkeypatch, db_session):
    monkeypatch.delenv('FCM_SERVER_KEY', raising=False)
    monkeypatch.delenv('FIREBASE_CREDENTIALS_JSON', raising=False)
    device = AgentDevice(
        system_id='android-push',
        status='approved',
        platform='android',
        fcm_token='token',
    )
    assert android_push_wake_available(device) is False

    monkeypatch.setenv('FCM_SERVER_KEY', 'test-key')
    assert android_push_wake_available(device) is True

    device.fcm_token = None
    assert android_push_wake_available(device) is False


def test_android_should_use_persistent_websocket(monkeypatch, db_session):
    monkeypatch.delenv('FCM_SERVER_KEY', raising=False)
    monkeypatch.delenv('FIREBASE_CREDENTIALS_JSON', raising=False)
    android = AgentDevice(
        system_id='android-persist',
        status='approved',
        platform='android',
        fcm_token='token',
    )
    linux = AgentDevice(system_id='linux-persist', status='approved', platform='linux')

    assert android_should_use_persistent_websocket(android) is True
    assert android_should_use_persistent_websocket(linux) is False

    monkeypatch.setenv('FCM_SERVER_KEY', 'test-key')
    assert android_should_use_persistent_websocket(android) is False


@patch('src.agent_push.notify_android_agent', return_value=(True, 'sent'))
def test_notify_device_message_uses_fcm_when_offline(mock_notify, app, db_session):
    device = AgentDevice(
        system_id='android-offline',
        status='approved',
        platform='android',
        fcm_token='token-xyz',
    )
    db_session.add(device)
    db_session.commit()

    with app.app_context():
        ok, message = notify_device_message(
            'android-offline',
            {'type': 'policy_sync_hint', 'reason': 'catalog_updated'},
        )
    assert ok is True
    mock_notify.assert_called_once()
