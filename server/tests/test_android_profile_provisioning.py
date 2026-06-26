"""Tests for Android user profile provisioning and device-owner metadata."""

import pytest

from src.agent.push import update_device_push_metadata
from src.models import AgentDevice, ManagedUser, ManagedUserDeviceMap, Settings
from src.user.manager import sync_mapping_linux_uids_from_device


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


@pytest.fixture
def android_device(db_session):
    device = AgentDevice(
        system_id='sys-profile-provision',
        status='approved',
        secure_token='token',
        platform='android',
        is_device_owner=False,
    )
    db_session.add(device)
    db_session.commit()
    return device


def test_hello_message_updates_is_device_owner(db_session, android_device):
    update_device_push_metadata(android_device, {'is_device_owner': True})
    assert android_device.is_device_owner is True

    update_device_push_metadata(android_device, {'is_device_owner': False})
    assert android_device.is_device_owner is False


def test_add_user_mapping_stores_android_profile_type(auth_client, android_device, db_session):
    user = ManagedUser(username='profile-child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.commit()

    response = auth_client.post(
        f'/managed-users/{user.id}/mappings/add',
        data={
            'system_id': android_device.system_id,
            'linux_username': 'jordan',
            'android_profile_type': 'restricted',
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    mapping = ManagedUserDeviceMap.query.filter_by(
        managed_user_id=user.id,
        system_id=android_device.system_id,
    ).one()
    assert mapping.linux_username == 'jordan'
    assert mapping.android_profile_type == 'restricted'


def test_add_user_mapping_ignores_invalid_profile_type(auth_client, android_device, db_session):
    user = ManagedUser(username='profile-child-2', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.commit()

    response = auth_client.post(
        f'/managed-users/{user.id}/mappings/add',
        data={
            'system_id': android_device.system_id,
            'linux_username': 'alex',
            'android_profile_type': 'invalid',
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    mapping = ManagedUserDeviceMap.query.filter_by(
        managed_user_id=user.id,
        system_id=android_device.system_id,
    ).one()
    assert mapping.android_profile_type is None


def test_sync_mapping_linux_uids_from_device_updates_stale_uid(db_session, android_device):
    user = ManagedUser(username='profile-child-3', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush()

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=android_device.system_id,
        linux_username='Child Profile',
        linux_uid=0,
        is_valid=False,
        android_profile_type='restricted',
    )
    db_session.add(mapping)
    android_device.linux_users_json = (
        '[{"username": "Child Profile", "uid": 15, "platform": "android"}]'
    )
    db_session.commit()

    updated = sync_mapping_linux_uids_from_device(android_device)
    db_session.commit()

    assert updated == {android_device.system_id}
    assert mapping.linux_uid == 15


def test_sync_mapping_linux_uids_leaves_unmatched_mappings(db_session, android_device):
    user = ManagedUser(username='profile-child-4', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush()

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=android_device.system_id,
        linux_username='Not Created Yet',
        linux_uid=None,
        is_valid=False,
        android_profile_type='standard',
    )
    db_session.add(mapping)
    android_device.linux_users_json = (
        '[{"username": "Other Profile", "uid": 12, "platform": "android"}]'
    )
    db_session.commit()

    updated = sync_mapping_linux_uids_from_device(android_device)
    assert updated == set()
    assert mapping.linux_uid is None


def test_agent_device_has_managed_profiles_property(db_session, android_device):
    # Initially no mappings, so has_managed_profiles should be False
    assert android_device.has_managed_profiles is False

    # Add a standard mapping without profile type
    user = ManagedUser(username='child-standard', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush()
    mapping_standard = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=android_device.system_id,
        linux_username='child_std',
        is_valid=True,
    )
    db_session.add(mapping_standard)
    db_session.commit()
    assert android_device.has_managed_profiles is False

    # Add a mapping with android_profile_type = 'restricted'
    user_restricted = ManagedUser(username='child-restricted', system_ip='Unassigned', is_valid=True)
    db_session.add(user_restricted)
    db_session.flush()
    mapping_restricted = ManagedUserDeviceMap(
        managed_user_id=user_restricted.id,
        system_id=android_device.system_id,
        linux_username='child_res',
        is_valid=True,
        android_profile_type='restricted',
    )
    db_session.add(mapping_restricted)
    db_session.commit()
    assert android_device.has_managed_profiles is True


def test_device_detail_locked_device_recovery_conditional_rendering(auth_client, android_device, db_session):
    # Log in first
    Settings.set_admin_password("admin")
    auth_client.post('/', data={'username': 'admin', 'password': 'admin'})

    # Case 1: No managed profiles. The recovery card should not be in the rendered HTML.
    res = auth_client.get(f'/devices/{android_device.system_id}')
    assert res.status_code == 200
    assert b"Locked Device Recovery" not in res.data

    # Case 2: Using managed profiles. The recovery card should be present.
    user = ManagedUser(username='child-res-ui', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=android_device.system_id,
        linux_username='child_res_ui',
        is_valid=True,
        android_profile_type='restricted',
    )
    db_session.add(mapping)
    db_session.commit()

    res = auth_client.get(f'/devices/{android_device.system_id}')
    assert res.status_code == 200
    assert b"Locked Device Recovery" in res.data
