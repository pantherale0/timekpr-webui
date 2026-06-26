"""Unit tests for linux_device_policy_manager."""

import pytest

from src.models import AgentDevice, ManagedUser, ManagedUserDeviceMap, MappingLinuxDevicePolicy
from src.policy.linux import (
    build_device_policy_payload,
    compute_revision,
    get_or_create_policy,
    upsert_policy,
)


@pytest.fixture
def linux_mapping(db_session):
    device = AgentDevice(system_id='sys-linux-device-policy', status='approved', secure_token='token')
    user = ManagedUser(username='linux-child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-linux-device-policy',
        linux_username='linux-child',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()
    return mapping


@pytest.fixture
def android_mapping(db_session):
    device = AgentDevice(
        system_id='sys-android-device-policy',
        status='approved',
        secure_token='token',
        platform='android',
    )
    user = ManagedUser(username='android-child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-android-device-policy',
        linux_username='android',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()
    return mapping


def test_build_device_policy_payload_defaults(linux_mapping):
    policy = get_or_create_policy(linux_mapping)
    payload = build_device_policy_payload(policy)
    assert payload == {
        'polkit': {
            'installSoftwareDisabled': False,
            'uninstallSoftwareDisabled': False,
            'mountRemovableMediaDisabled': False,
            'modifyAccountsDisabled': False,
            'systemPowerActionsDisabled': False,
            'pkexecElevationDisabled': False,
            'flatpakInstallDisabled': False,
            'snapInstallDisabled': False,
        },
        'connectivity': {
            'bluetoothDisabled': False,
        },
        'exec': {
            'terminalAccessDisabled': False,
        },
        'chrome': {
            'incognitoDisabled': True,
            'safeBrowsingEnforced': True,
            'youtubeRestrict': 2,
            'blockOtherExtensions': False,
            'blockGenaiFeatures': False,
            'allowedExtensionIds': [],
        },
        'supportMessage': MappingLinuxDevicePolicy.DEFAULT_SUPPORT_MESSAGE,
    }


def test_upsert_policy_chrome(linux_mapping, monkeypatch):
    monkeypatch.setattr(
        'src.policy.linux.push_mapping_device_policy',
        lambda mapping: (True, 'ok'),
    )
    policy = upsert_policy(linux_mapping, {
        'chrome_policies': {
            'incognito_disabled': False,
            'safesearch_enforced': False,
            'youtube_restrict': 1,
            'block_other_extensions': True,
            'block_genai_features': True,
        }
    })
    assert policy.chrome_policies['incognito_disabled'] is False
    assert policy.chrome_policies['safesearch_enforced'] is False
    assert policy.chrome_policies['youtube_restrict'] == 1
    assert policy.chrome_policies['block_other_extensions'] is True
    assert policy.chrome_policies['block_genai_features'] is True
    
    payload = build_device_policy_payload(policy)
    assert payload['chrome'] == {
        'incognitoDisabled': False,
        'safeBrowsingEnforced': False,
        'youtubeRestrict': 1,
        'blockOtherExtensions': True,
        'blockGenaiFeatures': True,
        'allowedExtensionIds': [],
    }


def test_compute_revision_is_stable(linux_mapping):
    policy = get_or_create_policy(linux_mapping)
    payload = build_device_policy_payload(policy)
    first = compute_revision(payload)
    second = compute_revision(payload)
    assert first == second
    assert len(first) == 64


def test_upsert_policy_updates_fields(linux_mapping, monkeypatch):
    monkeypatch.setattr(
        'src.policy.linux.push_mapping_device_policy',
        lambda mapping: (False, 'Agent offline'),
    )
    policy = upsert_policy(linux_mapping, {
        'install_software_disabled': True,
        'uninstall_software_disabled': True,
        'mount_removable_media_disabled': True,
        'modify_accounts_disabled': True,
        'system_power_actions_disabled': True,
        'pkexec_elevation_disabled': True,
        'bluetooth_disabled': True,
        'flatpak_install_disabled': True,
        'snap_install_disabled': True,
        'terminal_access_disabled': True,
    })
    assert policy.install_software_disabled is True
    assert policy.terminal_access_disabled is True
    assert policy.is_synced is False
    assert policy.last_sync_error == 'Agent offline'


def test_upsert_rejects_android_mapping(android_mapping):
    with pytest.raises(ValueError, match='Linux device policy'):
        upsert_policy(android_mapping, {'install_software_disabled': True})


def test_upsert_support_message(linux_mapping, monkeypatch):
    monkeypatch.setattr(
        'src.policy.linux.push_mapping_device_policy',
        lambda mapping: (True, 'ok'),
    )
    policy = upsert_policy(linux_mapping, {
        'support_message': 'Ask your parent to change this in TimeKpr.',
    })
    assert 'parent' in policy.support_message.lower()
    payload = build_device_policy_payload(policy)
    assert payload['supportMessage'] == policy.support_message


def test_upsert_rejects_empty_support_message(linux_mapping, monkeypatch):
    monkeypatch.setattr(
        'src.policy.linux.push_mapping_device_policy',
        lambda mapping: (True, 'ok'),
    )
    with pytest.raises(ValueError, match='support_message'):
        upsert_policy(linux_mapping, {'support_message': '   '})


def test_upsert_policy_chrome_extensions(linux_mapping, monkeypatch):
    monkeypatch.setattr(
        'src.policy.linux.push_mapping_device_policy',
        lambda mapping: (True, 'ok'),
    )
    
    # 1. Test valid allowed extension IDs as list
    policy = upsert_policy(linux_mapping, {
        'chrome_policies': {
            'allowed_extension_ids': ['gnokihbalbffklhnhamjompcmbgojmjp', 'gcbomnkjlpaaappabaakgddmghngejim']
        }
    })
    assert policy.chrome_policies['allowed_extension_ids'] == [
        'gnokihbalbffklhnhamjompcmbgojmjp',
        'gcbomnkjlpaaappabaakgddmghngejim'
    ]
    
    payload = build_device_policy_payload(policy)
    assert payload['chrome']['allowedExtensionIds'] == [
        'gnokihbalbffklhnhamjompcmbgojmjp',
        'gcbomnkjlpaaappabaakgddmghngejim'
    ]
    
    # 2. Test valid allowed extension IDs as comma/space-separated string
    policy2 = upsert_policy(linux_mapping, {
        'chrome_policies': {
            'allowed_extension_ids': 'gnokihbalbffklhnhamjompcmbgojmjp, gcbomnkjlpaaappabaakgddmghngejim'
        }
    })
    assert policy2.chrome_policies['allowed_extension_ids'] == [
        'gnokihbalbffklhnhamjompcmbgojmjp',
        'gcbomnkjlpaaappabaakgddmghngejim'
    ]
    
    # 3. Test invalid extension ID raises ValueError
    import pytest
    with pytest.raises(ValueError, match='Invalid Chrome Extension ID'):
        upsert_policy(linux_mapping, {
            'chrome_policies': {
                'allowed_extension_ids': ['invalid_id_123']
            }
        })
