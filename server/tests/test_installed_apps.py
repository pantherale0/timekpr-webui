"""Tests for installed application inventory reporting."""

import base64
import hashlib

import pytest

from src.models import AgentDevice, ApplicationIcon, DeviceInstalledApplication, ManagedUser, ManagedUserDeviceMap
from src.device.installed_apps import (
    finalize_report,
    get_icon,
    handle_app_icon_report,
    handle_installed_apps_report,
    ingest_chunk,
    list_installed_apps_for_device,
    list_installed_apps_for_managed_user,
    normalize_installed_app_entry,
    store_icon,
)


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_HASH = hashlib.sha256(PNG_BYTES).hexdigest()


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as session_state:
        session_state['logged_in'] = True
    return client


@pytest.fixture
def approved_device(db_session):
    device = AgentDevice(
        system_id='device-inv-1',
        system_hostname='test-host',
        status='approved',
        platform='linux',
    )
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def managed_mapping(db_session, approved_device):
    user = ManagedUser(username='child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.commit()

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=approved_device.system_id,
        linux_username='alice',
        linux_uid=1000,
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()
    return user, mapping


def test_normalize_linux_executable(approved_device):
    entry = normalize_installed_app_entry('linux', {
        'application_name': 'Firefox',
        'identifier': '/usr/bin/firefox',
        'match_type': 'executable',
        'version_name': '128.0',
    })
    assert entry['identifier'] == '/usr/bin/firefox'
    assert entry['match_type'] == 'executable'


def test_normalize_android_package(db_session, approved_device):
    approved_device.platform = 'android'
    db_session.commit()

    entry = normalize_installed_app_entry('android', {
        'application_name': 'Demo',
        'identifier': 'com.example.demo',
        'match_type': 'package',
    })
    assert entry['identifier'] == '/android/package/com.example.demo'


def test_finalize_report_upserts_and_marks_stale(db_session, approved_device):
    report_id = 'report-1'
    apps = [{
        'application_name': 'Firefox',
        'identifier': '/usr/bin/firefox',
        'match_type': 'executable',
        'platform': 'linux',
        'version_name': '128.0',
        'icon_hash': None,
    }]

    ingest_chunk(approved_device.system_id, report_id, 'alice', apps)
    result = finalize_report(approved_device.system_id, report_id)

    assert result['apps_total'] == 1
    row = DeviceInstalledApplication.query.filter_by(
        system_id=approved_device.system_id,
        identifier='/usr/bin/firefox',
    ).one()
    assert row.is_present is True

    ingest_chunk(approved_device.system_id, 'report-2', 'alice', [])
    finalize_report(approved_device.system_id, 'report-2')
    db_session.refresh(row)
    assert row.is_present is False


def test_icon_store_deduplicates(db_session):
    store_icon(PNG_HASH, 'image/png', PNG_BYTES)
    store_icon(PNG_HASH, 'image/png', PNG_BYTES)
    assert ApplicationIcon.query.count() == 1
    assert get_icon(PNG_HASH) is not None


def test_handle_installed_apps_report_message(db_session, approved_device):
    message = {
        'report_id': 'chunked-report',
        'linux_username': 'alice',
        'chunk_index': 0,
        'chunk_total': 1,
        'is_final': True,
        'reported_at': '2026-06-05T12:00:00Z',
        'apps': [{
            'application_name': 'Steam',
            'identifier': '/usr/bin/steam',
            'match_type': 'executable',
        }],
    }
    result = handle_installed_apps_report(approved_device.system_id, message)
    assert result['apps_total'] == 1
    apps = list_installed_apps_for_device(approved_device.system_id, linux_username='alice')
    assert len(apps) == 1


def test_handle_app_icon_report(db_session):
    result = handle_app_icon_report({
        'content_hash': PNG_HASH,
        'mime_type': 'image/png',
        'data_base64': base64.b64encode(PNG_BYTES).decode('ascii'),
    })
    assert result['success'] is True


def test_list_installed_apps_for_managed_user(db_session, managed_mapping):
    user, mapping = managed_mapping
    report_id = 'user-report'
    ingest_chunk(mapping.system_id, report_id, mapping.linux_username, [{
        'application_name': 'Discord',
        'identifier': '/usr/bin/discord',
        'match_type': 'executable',
        'version_name': None,
        'icon_hash': None,
    }])
    finalize_report(mapping.system_id, report_id)

    apps = list_installed_apps_for_managed_user(user.id)
    assert len(apps) == 1
    assert apps[0]['application_name'] == 'Discord'


def test_api_device_installed_apps(auth_client, approved_device):
    report_id = 'api-report'
    ingest_chunk(approved_device.system_id, report_id, 'alice', [{
        'application_name': 'VLC',
        'identifier': '/usr/bin/vlc',
        'match_type': 'executable',
        'version_name': None,
        'icon_hash': None,
    }])
    finalize_report(approved_device.system_id, report_id)

    response = auth_client.get(f'/api/devices/{approved_device.system_id}/installed-apps')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert len(payload['apps']) == 1


def test_api_icon_endpoint(client, db_session):
    store_icon(PNG_HASH, 'image/png', PNG_BYTES)
    response = client.get(f'/api/apps/icons/{PNG_HASH}')
    assert response.status_code == 200
    assert response.data == PNG_BYTES
