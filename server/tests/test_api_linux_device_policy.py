"""API tests for Linux device restriction policies."""

import pytest

from src.database import AgentDevice, ManagedUser, ManagedUserDeviceMap


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


@pytest.fixture
def linux_policy_fixture(db_session):
    device = AgentDevice(
        system_id='sys-api-linux-device-policy',
        status='approved',
        secure_token='token',
    )
    user = ManagedUser(username='api-linux-child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-api-linux-device-policy',
        linux_username='child',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()
    return {'user': user, 'mapping': mapping}


def test_get_linux_device_policy_requires_auth(client, linux_policy_fixture):
    mapping_id = linux_policy_fixture['mapping'].id
    response = client.get(f'/api/mappings/{mapping_id}/linux-device-policy')
    assert response.status_code == 401


def test_get_linux_device_policy_defaults(auth_client, linux_policy_fixture):
    mapping_id = linux_policy_fixture['mapping'].id
    response = auth_client.get(f'/api/mappings/{mapping_id}/linux-device-policy')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['policy']['install_software_disabled'] is False
    assert payload['policy']['device_policy']['exec']['terminalAccessDisabled'] is False


def test_put_linux_device_policy(auth_client, linux_policy_fixture, monkeypatch):
    mapping_id = linux_policy_fixture['mapping'].id
    monkeypatch.setattr(
        'src.linux_device_policy_manager.push_mapping_device_policy',
        lambda mapping: (False, 'Agent offline'),
    )
    response = auth_client.put(
        f'/api/mappings/{mapping_id}/linux-device-policy',
        json={
            'install_software_disabled': True,
            'terminal_access_disabled': True,
            'pkexec_elevation_disabled': True,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['policy']['install_software_disabled'] is True
    assert payload['policy']['terminal_access_disabled'] is True
    assert payload['policy']['is_synced'] is False
    assert 'sync pending' in payload['message'].lower()


def test_put_rejects_android_mapping(auth_client, db_session):
    device = AgentDevice(
        system_id='sys-api-android-linux-policy',
        status='approved',
        secure_token='token',
        platform='android',
    )
    user = ManagedUser(username='api-android-child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-api-android-linux-policy',
        linux_username='android',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()

    response = auth_client.put(
        f'/api/mappings/{mapping.id}/linux-device-policy',
        json={'install_software_disabled': True},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['success'] is False
