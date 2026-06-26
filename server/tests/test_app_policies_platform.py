"""Tests for platform-specific app policy profiles."""

import pytest

from src.policy.apparmor import (
    compile_user_apparmor_rules,
    validate_policy_rule_for_platform,
    _build_apparmor_policy_sync_payload,
)
from src.models import (
    AgentDevice,
    AppArmorRule,
    AppPolicy,
    AppPolicyRule,
    ManagedUser,
    ManagedUserAppPolicyAssignment,
    ManagedUserDeviceMap,
)
from src.device.installed_apps import finalize_report, ingest_chunk, list_discovered_apps_for_platform


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as session_state:
        session_state['logged_in'] = True
    return client


@pytest.fixture
def linux_device(db_session):
    device = AgentDevice(system_id='linux-dev-1', status='approved', platform='linux')
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def android_device(db_session):
    device = AgentDevice(system_id='android-dev-1', status='approved', platform='android')
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def dual_device_user(db_session, linux_device, android_device):
    user = ManagedUser(username='dual-child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.commit()

    linux_map = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=linux_device.system_id,
        linux_username='alice',
        linux_uid=1000,
        is_valid=True,
    )
    android_map = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=android_device.system_id,
        linux_username='android-child',
        linux_uid=1001,
        is_valid=True,
    )
    db_session.add(linux_map)
    db_session.add(android_map)
    db_session.commit()
    return user, linux_map, android_map


def test_list_discovered_apps_for_platform_filters_by_device(db_session, linux_device, android_device):
    ingest_chunk(linux_device.system_id, 'linux-report', 'alice', [{
        'application_name': 'Firefox',
        'identifier': '/usr/bin/firefox',
        'match_type': 'executable',
        'version_name': None,
        'icon_hash': None,
    }])
    finalize_report(linux_device.system_id, 'linux-report')

    ingest_chunk(android_device.system_id, 'android-report', 'android-child', [{
        'application_name': 'Demo App',
        'identifier': '/android/package/com.example.demo',
        'match_type': 'package',
        'version_name': '1.0',
        'icon_hash': None,
    }])
    finalize_report(android_device.system_id, 'android-report')

    linux_apps = list_discovered_apps_for_platform(AppPolicy.PLATFORM_LINUX)
    android_apps = list_discovered_apps_for_platform(AppPolicy.PLATFORM_ANDROID)

    assert len(linux_apps) == 1
    assert linux_apps[0]['identifier'] == '/usr/bin/firefox'
    assert len(android_apps) == 1
    assert android_apps[0]['identifier'] == '/android/package/com.example.demo'


def test_validate_policy_rule_rejects_executable_on_android_policy():
    with pytest.raises(ValueError, match='Android policies only support package rules'):
        validate_policy_rule_for_platform(
            AppPolicy.PLATFORM_ANDROID,
            AppPolicyRule.MATCH_TYPE_EXECUTABLE,
            AppPolicyRule.PRESET_BLOCKED,
            '/usr/bin/firefox',
        )


def test_validate_policy_rule_rejects_package_on_linux_policy():
    with pytest.raises(ValueError, match='Linux policies only support'):
        validate_policy_rule_for_platform(
            AppPolicy.PLATFORM_LINUX,
            AppPolicyRule.MATCH_TYPE_PACKAGE,
            AppPolicyRule.PRESET_BLOCKED,
            'com.example.demo',
        )


def test_validate_policy_rule_rejects_no_internet_on_android():
    with pytest.raises(ValueError, match='no_internet'):
        validate_policy_rule_for_platform(
            AppPolicy.PLATFORM_ANDROID,
            AppPolicyRule.MATCH_TYPE_PACKAGE,
            AppPolicyRule.PRESET_NO_INTERNET,
            'com.example.demo',
        )


def test_compile_user_apparmor_rules_filters_by_device_platform(db_session, dual_device_user):
    user, linux_map, android_map = dual_device_user

    linux_policy = AppPolicy(name='Linux Blocks', platform=AppPolicy.PLATFORM_LINUX)
    android_policy = AppPolicy(name='Android Blocks', platform=AppPolicy.PLATFORM_ANDROID)
    db_session.add(linux_policy)
    db_session.add(android_policy)
    db_session.commit()

    db_session.add(AppPolicyRule(
        policy_id=linux_policy.id,
        application_name='Firefox',
        executable_path='/usr/bin/firefox',
        match_type=AppPolicyRule.MATCH_TYPE_EXECUTABLE,
        preset=AppPolicyRule.PRESET_BLOCKED,
    ))
    db_session.add(AppPolicyRule(
        policy_id=android_policy.id,
        application_name='Demo App',
        executable_path='/android/package/com.example.demo',
        match_type=AppPolicyRule.MATCH_TYPE_PACKAGE,
        preset=AppPolicyRule.PRESET_BLOCKED,
    ))
    db_session.add(ManagedUserAppPolicyAssignment(managed_user_id=user.id, policy_id=linux_policy.id))
    db_session.add(ManagedUserAppPolicyAssignment(managed_user_id=user.id, policy_id=android_policy.id))
    db_session.commit()

    compile_user_apparmor_rules(user)

    linux_rules = AppArmorRule.query.filter_by(device_map_id=linux_map.id).all()
    android_rules = AppArmorRule.query.filter_by(device_map_id=android_map.id).all()

    assert len(linux_rules) == 1
    assert linux_rules[0].executable_path == '/usr/bin/firefox'
    assert len(android_rules) == 1
    assert android_rules[0].executable_path == '/android/package/com.example.demo'


def test_compile_user_apparmor_rules_replaces_rules_when_adding_second_app(db_session, dual_device_user):
    user, _linux_map, android_map = dual_device_user

    android_policy = AppPolicy(name='Android Blocks', platform=AppPolicy.PLATFORM_ANDROID)
    db_session.add(android_policy)
    db_session.commit()

    db_session.add(AppPolicyRule(
        policy_id=android_policy.id,
        application_name='Contacts',
        executable_path='/android/package/com.android.contacts',
        match_type=AppPolicyRule.MATCH_TYPE_PACKAGE,
        preset=AppPolicyRule.PRESET_BLOCKED,
    ))
    db_session.add(ManagedUserAppPolicyAssignment(managed_user_id=user.id, policy_id=android_policy.id))
    db_session.commit()

    compile_user_apparmor_rules(user)
    assert AppArmorRule.query.filter_by(device_map_id=android_map.id).count() == 1

    db_session.add(AppPolicyRule(
        policy_id=android_policy.id,
        application_name='Phone',
        executable_path='/android/package/com.android.dialer',
        match_type=AppPolicyRule.MATCH_TYPE_PACKAGE,
        preset=AppPolicyRule.PRESET_BLOCKED,
    ))
    db_session.commit()

    compile_user_apparmor_rules(user)

    paths = {
        rule.executable_path
        for rule in AppArmorRule.query.filter_by(device_map_id=android_map.id).all()
    }
    assert paths == {
        '/android/package/com.android.contacts',
        '/android/package/com.android.dialer',
    }


def test_build_sync_payload_skips_incompatible_rules(db_session, dual_device_user):
    user, linux_map, android_map = dual_device_user

    db_session.add(AppArmorRule(
        device_map_id=linux_map.id,
        application_name='Firefox',
        executable_path='/usr/bin/firefox',
        match_type=AppArmorRule.MATCH_TYPE_EXECUTABLE,
        preset=AppArmorRule.PRESET_BLOCKED,
    ))
    db_session.add(AppArmorRule(
        device_map_id=linux_map.id,
        application_name='Wrong Platform',
        executable_path='/android/package/com.example.demo',
        match_type=AppArmorRule.MATCH_TYPE_PACKAGE,
        preset=AppArmorRule.PRESET_BLOCKED,
    ))
    db_session.commit()

    policies_list, skipped = _build_apparmor_policy_sync_payload(linux_map)

    assert len(policies_list) == 1
    assert policies_list[0]['executable_path'] == '/usr/bin/firefox'
    assert skipped == ['Wrong Platform']


def test_create_app_policy_with_platform(auth_client, db_session):
    response = auth_client.post(
        '/admin/app-policies/create',
        data={'name': 'Android Gaming Lock', 'platform': 'android'},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    policy = AppPolicy.query.filter_by(name='Android Gaming Lock').one()
    assert policy.platform == AppPolicy.PLATFORM_ANDROID


def test_add_android_rule_via_post(auth_client, db_session):
    policy = AppPolicy(name='Android Test', platform=AppPolicy.PLATFORM_ANDROID)
    db_session.add(policy)
    db_session.commit()

    response = auth_client.post(
        f'/admin/app-policies/{policy.id}/rule/add',
        data={
            'application_name': 'Demo App',
            'match_type': 'package',
            'executable_path': 'com.example.demo',
            'preset': 'blocked',
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    rule = AppPolicyRule.query.filter_by(policy_id=policy.id).one()
    assert rule.match_type == AppPolicyRule.MATCH_TYPE_PACKAGE
    assert rule.executable_path == '/android/package/com.example.demo'


def test_reject_executable_rule_on_android_policy_post(auth_client, db_session):
    policy = AppPolicy(name='Android Reject', platform=AppPolicy.PLATFORM_ANDROID)
    db_session.add(policy)
    db_session.commit()

    response = auth_client.post(
        f'/admin/app-policies/{policy.id}/rule/add',
        data={
            'application_name': 'Firefox',
            'match_type': 'executable',
            'executable_path': '/usr/bin/firefox',
            'preset': 'blocked',
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert AppPolicyRule.query.filter_by(policy_id=policy.id).count() == 0
