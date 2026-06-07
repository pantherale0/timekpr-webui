"""Unit tests for android_device_policy_manager."""

import pytest

from src.android_device_policy_manager import (
    build_device_policy_payload,
    compute_revision,
    get_or_create_policy,
    upsert_policy,
)
from src.database import AgentDevice, ManagedUser, ManagedUserDeviceMap, MappingAndroidDevicePolicy


@pytest.fixture
def android_device(db_session):
    device = AgentDevice(
        system_id='sys-android-policy',
        status='approved',
        secure_token='token',
        platform='android',
    )
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def android_mapping(db_session, android_device):
    user = ManagedUser(username='android-child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-android-policy',
        linux_username='android',
        is_valid=True,
        android_profile_type='restricted',
    )
    db_session.add(mapping)
    db_session.commit()
    return mapping


@pytest.fixture
def linux_device(db_session):
    device = AgentDevice(
        system_id='sys-linux-policy', 
        status='approved', 
        secure_token='token',
        platform='linux',
    )
    db_session.add(device)
    db_session.commit()
    return device


def test_build_device_policy_payload_defaults(android_device):
    policy = get_or_create_policy(android_device)
    payload = build_device_policy_payload(policy)
    assert payload == {
        'screenCaptureDisabled': False,
        'cameraAccess': MappingAndroidDevicePolicy.CAMERA_ACCESS_UNSPECIFIED,
        'microphoneAccess': MappingAndroidDevicePolicy.MICROPHONE_ACCESS_UNSPECIFIED,
        'installAppsDisabled': False,
        'uninstallAppsDisabled': False,
        'factoryResetDisabled': False,
        'adjustVolumeDisabled': False,
        'modifyAccountsDisabled': False,
        'mountPhysicalMediaDisabled': False,
        'bluetoothDisabled': False,
        'outgoingCallsDisabled': False,
        'smsDisabled': False,
        'blockNfc': False,
        'blockWifiTethering': False,
        'advancedSecurityOverrides': {
            'developerSettings': MappingAndroidDevicePolicy.DEVELOPER_SETTINGS_UNSPECIFIED,
        },
        'deviceConnectivityManagement': {
            'usbDataAccess': MappingAndroidDevicePolicy.USB_DATA_ACCESS_UNSPECIFIED,
        },
        'shortSupportMessage': {
            'defaultMessage': MappingAndroidDevicePolicy.DEFAULT_SHORT_SUPPORT_MESSAGE,
        },
        'longSupportMessage': {
            'defaultMessage': MappingAndroidDevicePolicy.DEFAULT_LONG_SUPPORT_MESSAGE,
        },
        'profiles': [],
        'lockOwnerProfile': False,
        'managedProfileUids': [],
    }


def test_build_device_policy_payload_with_profiles(android_device, android_mapping):
    policy = get_or_create_policy(android_device)
    payload = build_device_policy_payload(policy)
    assert payload['profiles'] == [
        {
            'username': 'android',
            'profile_type': 'restricted'
        }
    ]


def test_build_device_policy_payload_locks_unassigned_owner(android_device, android_mapping, db_session):
    android_mapping.linux_uid = 15
    db_session.commit()
    policy = get_or_create_policy(android_device)
    payload = build_device_policy_payload(policy)
    assert payload['managedProfileUids'] == [15]
    assert payload['lockOwnerProfile'] is True


def test_build_device_policy_payload_skips_owner_lock_when_owner_mapped(android_device, db_session):
    user = ManagedUser(username='owner-child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush()
    child = ManagedUser(username='other-child', system_ip='Unassigned', is_valid=True)
    db_session.add(child)
    db_session.flush()
    db_session.add_all([
        ManagedUserDeviceMap(
            managed_user_id=user.id,
            system_id=android_device.system_id,
            linux_username='Owner',
            linux_uid=0,
            is_valid=True,
            android_profile_type='standard',
        ),
        ManagedUserDeviceMap(
            managed_user_id=child.id,
            system_id=android_device.system_id,
            linux_username='Child',
            linux_uid=15,
            is_valid=True,
            android_profile_type='restricted',
        ),
    ])
    db_session.commit()
    policy = get_or_create_policy(android_device)
    payload = build_device_policy_payload(policy)
    assert payload['managedProfileUids'] == [15]
    assert payload['lockOwnerProfile'] is False


def test_build_device_policy_payload_skips_linked_profiles(android_device, android_mapping, db_session):
    android_mapping.linux_uid = 11
    db_session.commit()
    policy = get_or_create_policy(android_device)
    payload = build_device_policy_payload(policy)
    assert payload['profiles'] == []


def test_compute_revision_is_stable(android_device):
    policy = get_or_create_policy(android_device)
    payload = build_device_policy_payload(policy)
    first = compute_revision(payload)
    second = compute_revision(payload)
    assert first == second
    assert len(first) == 64


def test_upsert_policy_updates_fields(android_device, monkeypatch):
    monkeypatch.setattr(
        'src.android_device_policy_manager.push_device_policy',
        lambda device: (False, 'Agent offline'),
    )
    policy = upsert_policy(android_device, {
        'screen_capture_disabled': True,
        'camera_access': 'CAMERA_ACCESS_DISABLED',
        'microphone_access': 'MICROPHONE_ACCESS_DISABLED',
        'install_apps_disabled': True,
        'uninstall_apps_disabled': False,
        'factory_reset_disabled': True,
        'bluetooth_disabled': True,
        'usb_data_access': 'DISALLOW_USB_FILE_TRANSFER',
        'developer_settings': 'DEVELOPER_SETTINGS_DISABLED',
    })
    assert policy.screen_capture_disabled is True
    assert policy.camera_access == MappingAndroidDevicePolicy.CAMERA_ACCESS_DISABLED
    assert policy.microphone_access == MappingAndroidDevicePolicy.MICROPHONE_ACCESS_DISABLED
    assert policy.install_apps_disabled is True
    assert policy.factory_reset_disabled is True
    assert policy.bluetooth_disabled is True
    assert policy.usb_data_access == MappingAndroidDevicePolicy.USB_DATA_ACCESS_DISALLOW_FILE
    assert policy.developer_settings == MappingAndroidDevicePolicy.DEVELOPER_SETTINGS_DISABLED
    assert policy.is_synced is False
    assert policy.last_sync_error == 'Agent offline'


def test_upsert_rejects_linux_mapping(linux_device):
    with pytest.raises(ValueError, match='Android device policy'):
        upsert_policy(linux_device, {'screen_capture_disabled': True})


def test_upsert_support_messages_use_parental_controls_wording(android_device, monkeypatch):
    monkeypatch.setattr(
        'src.android_device_policy_manager.push_device_policy',
        lambda device: (True, 'ok'),
    )
    policy = upsert_policy(android_device, {
        'short_support_message': 'Ask your parent to change this in TimeKpr.',
        'long_support_message': 'TimeKpr parental controls protect this device.',
    })
    assert 'parent' in policy.short_support_message.lower()
    assert 'parental controls' in policy.long_support_message.lower()
    payload = build_device_policy_payload(policy)
    assert payload['shortSupportMessage']['defaultMessage'] == policy.short_support_message
    assert payload['longSupportMessage']['defaultMessage'] == policy.long_support_message


def test_upsert_rejects_empty_support_message(android_device, monkeypatch):
    monkeypatch.setattr(
        'src.android_device_policy_manager.push_device_policy',
        lambda device: (True, 'ok'),
    )
    with pytest.raises(ValueError, match='short_support_message'):
        upsert_policy(android_device, {'short_support_message': '   '})


def test_upsert_rejects_invalid_camera_access(android_device, monkeypatch):
    monkeypatch.setattr(
        'src.android_device_policy_manager.push_device_policy',
        lambda device: (True, 'ok'),
    )
    with pytest.raises(ValueError, match='camera_access'):
        upsert_policy(android_device, {'camera_access': 'INVALID'})


def test_upsert_rejects_invalid_usb_data_access(android_device, monkeypatch):
    monkeypatch.setattr(
        'src.android_device_policy_manager.push_device_policy',
        lambda device: (True, 'ok'),
    )
    with pytest.raises(ValueError, match='usb_data_access'):
        upsert_policy(android_device, {'usb_data_access': 'INVALID'})
