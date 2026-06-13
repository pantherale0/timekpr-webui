"""Tests for Linux screenshot recall storage and settings."""

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from src.database import AgentDevice, DeviceRecallSettings, DeviceScreenshot
from src.recall_manager import (
    build_recall_policy_payload,
    compute_revision,
    get_or_create_settings,
    upsert_settings,
)
from src.screenshot_manager import (
    handle_screenshot_report,
    list_screenshots_for_device,
    normalize_screenshot_report,
    prune_expired_screenshots,
)

_TINY_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
)


@pytest.fixture
def linux_device(db_session):
    device = AgentDevice(system_id='linux-recall-1', status='approved', platform='linux')
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def android_device(db_session):
    device = AgentDevice(system_id='android-recall-1', status='approved', platform='android')
    db_session.add(device)
    db_session.commit()
    return device


def _build_report(system_id, screenshot_id='11111111-1111-4111-8111-111111111111'):
    content_hash = hashlib.sha256(_TINY_PNG).hexdigest()
    return {
        'type': 'screenshot_report',
        'screenshot_id': screenshot_id,
        'linux_username': 'child',
        'captured_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'mime_type': 'image/png',
        'width': 1,
        'height': 1,
        'content_hash': content_hash,
        'active_window_title': 'Test Window',
        'data_base64': base64.b64encode(_TINY_PNG).decode('ascii'),
    }


def test_get_or_create_recall_settings(linux_device, db_session):
    settings = get_or_create_settings(linux_device)
    db_session.commit()

    assert settings.enabled is False
    assert settings.interval_seconds == DeviceRecallSettings.DEFAULT_INTERVAL_SECONDS
    assert settings.retention_hours == DeviceRecallSettings.DEFAULT_RETENTION_HOURS


def test_upsert_recall_settings_updates_revision(linux_device, db_session):
    settings = upsert_settings(linux_device, {
        'enabled': True,
        'interval_seconds': 120,
        'retention_hours': 48,
    })
    db_session.commit()

    assert settings.enabled is True
    assert settings.interval_seconds == 120
    assert settings.retention_hours == 48
    expected = build_recall_policy_payload(settings)
    assert settings.revision == compute_revision(expected)


def test_normalize_and_store_screenshot_report(linux_device, db_session):
    payload = _build_report(linux_device.system_id)
    normalized = normalize_screenshot_report(linux_device.system_id, payload)
    assert normalized['linux_username'] == 'child'
    assert normalized['mime_type'] == 'image/png'

    result = handle_screenshot_report(linux_device.system_id, payload)
    assert result['success'] is True
    assert result['duplicate'] is False

    listed = list_screenshots_for_device(linux_device.system_id)
    assert listed['total'] == 1
    assert listed['items'][0]['active_window_title'] == 'Test Window'


def test_duplicate_screenshot_report_is_idempotent(linux_device, db_session):
    payload = _build_report(linux_device.system_id)
    handle_screenshot_report(linux_device.system_id, payload)
    duplicate = handle_screenshot_report(linux_device.system_id, payload)
    assert duplicate['duplicate'] is True
    assert list_screenshots_for_device(linux_device.system_id)['total'] == 1


def test_prune_expired_screenshots(linux_device, db_session):
    payload = _build_report(
        linux_device.system_id,
        screenshot_id='22222222-2222-4222-8222-222222222222',
    )
    handle_screenshot_report(linux_device.system_id, payload)
    screenshot = DeviceScreenshot.query.filter_by(system_id=linux_device.system_id).first()
    screenshot.captured_at = datetime.now(timezone.utc) - timedelta(hours=48)
    db_session.commit()

    deleted = prune_expired_screenshots(linux_device.system_id, retention_hours=24)
    assert deleted == 1
    assert list_screenshots_for_device(linux_device.system_id)['total'] == 0


def test_recall_settings_rejected_for_android(android_device, db_session):
    with pytest.raises(ValueError, match='only supported for Linux'):
        get_or_create_settings(android_device)
