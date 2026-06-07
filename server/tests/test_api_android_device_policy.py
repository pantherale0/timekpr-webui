"""API tests for Android device restriction policies."""

import pytest

from src.database import AgentDevice, ManagedUser, ManagedUserDeviceMap


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


@pytest.fixture
def android_policy_fixture(db_session):
    device = AgentDevice(
        system_id='sys-api-android-policy',
        status='approved',
        secure_token='token',
        platform='android',
    )
    user = ManagedUser(username='api-android-child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-api-android-policy',
        linux_username='android',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()
    return {'user': user, 'mapping': mapping}


def test_get_android_device_policy_requires_auth(client, android_policy_fixture):
    mapping_id = android_policy_fixture['mapping'].id
    response = client.get(f'/api/mappings/{mapping_id}/android-device-policy')
    assert response.status_code == 401


def test_get_android_device_policy_defaults(auth_client, android_policy_fixture):
    system_id = android_policy_fixture['mapping'].system_id
    response = auth_client.get(f'/api/devices/{system_id}/android-device-policy')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['policy']['camera_access'] == 'CAMERA_ACCESS_UNSPECIFIED'
    assert payload['policy']['device_policy']['screenCaptureDisabled'] is False


def test_put_android_device_policy(auth_client, android_policy_fixture, monkeypatch):
    system_id = android_policy_fixture['mapping'].system_id
    monkeypatch.setattr(
        'src.android_device_policy_manager.push_device_policy',
        lambda device: (False, 'Agent offline'),
    )
    response = auth_client.put(
        f'/api/devices/{system_id}/android-device-policy',
        json={
            'screen_capture_disabled': True,
            'camera_access': 'CAMERA_ACCESS_DISABLED',
            'install_apps_disabled': True,
            'developer_settings': 'DEVELOPER_SETTINGS_DISABLED',
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['policy']['screen_capture_disabled'] is True
    assert payload['policy']['is_synced'] is False
    assert 'sync pending' in payload['message'].lower()


def test_put_rejects_linux_mapping(auth_client, db_session):
    device = AgentDevice(system_id='sys-api-linux-policy', status='approved', secure_token='token')
    user = ManagedUser(username='api-linux-child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-api-linux-policy',
        linux_username='child',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()

    response = auth_client.put(
        f'/api/mappings/{mapping.id}/android-device-policy',
        json={'screen_capture_disabled': True},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['success'] is False
