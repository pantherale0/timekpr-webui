import pytest

from src.models import (
    AgentDevice,
    AppArmorRule,
    BlocklistSource,
    ManagedUser,
    ManagedUserBlocklistAssignment,
    ManagedUserDeviceMap,
    MappingAndroidDevicePolicy,
    MappingApprovalSettings,
    MappingLinuxDevicePolicy,
    UserWeeklySchedule,
)
from src.policy.presets import (
    VALID_AGE_BRACKETS,
    VALID_MATURITY_LEVELS,
    apply_policy_preset,
    get_policy_bundle,
    get_matrix_metadata_for_ui,
    load_policy_preset_matrix,
)


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


@pytest.fixture
def child_user(db_session):
    user = ManagedUser(username='preset_child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def linux_mapping(db_session, child_user):
    device = AgentDevice(
        system_id='sys-policy-preset-linux',
        status='approved',
        secure_token='tok',
        platform='linux',
    )
    db_session.add(device)
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=child_user.id,
        system_id=device.system_id,
        linux_username='linux-child',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()
    return mapping


@pytest.fixture
def android_mapping(db_session, child_user):
    device = AgentDevice(
        system_id='sys-policy-preset-android',
        status='approved',
        secure_token='tok-android',
        platform='android',
    )
    db_session.add(device)
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=child_user.id,
        system_id=device.system_id,
        linux_username='android-child',
        is_valid=True,
        android_profile_type='standard',
    )
    db_session.add(mapping)
    db_session.commit()
    return mapping


def test_load_matrix_has_twelve_bundles():
    matrix = load_policy_preset_matrix()
    bundles = matrix.get('bundles') or {}
    assert len(bundles) == 12
    for age in VALID_AGE_BRACKETS:
        for maturity in VALID_MATURITY_LEVELS:
            assert f'{age}_{maturity}' in bundles


def test_get_policy_bundle_invalid():
    with pytest.raises(ValueError, match='Invalid policy_age_bracket'):
        get_policy_bundle('invalid', 'low')
    with pytest.raises(ValueError, match='Invalid policy_maturity_level'):
        get_policy_bundle('under7', 'extreme')


def test_get_matrix_metadata_for_ui():
    meta = get_matrix_metadata_for_ui()
    assert 'under7' in meta['age_brackets']
    assert 'high' in meta['maturity_levels']
    assert 'under7_high' in meta['bundles']
    assert 'android_device_policy' in meta['bundles']['under7_high']


def test_all_bundles_define_android_device_policy():
    matrix = load_policy_preset_matrix()
    for key, bundle in (matrix.get('bundles') or {}).items():
        android = bundle.get('android_device_policy')
        assert android is not None, f'missing android_device_policy in {key}'
        assert 'developer_settings' in android


def test_apply_policy_preset_profile_fields(app, db_session, child_user):
    with app.app_context():
        result = apply_policy_preset(child_user, '8_12', 'medium')

    db_session.refresh(child_user)
    assert child_user.policy_age_bracket == '8_12'
    assert child_user.policy_maturity_level == 'medium'
    assert child_user.overlay_age_tier == 'eight12'
    assert result['marketplace_preset_ids']
    assert 'vpn_proxy_bypass' in result['marketplace_preset_ids']


def test_apply_policy_preset_marketplace_and_schedule(app, db_session, child_user):
    bundle = get_policy_bundle('under7', 'low')

    with app.app_context():
        apply_policy_preset(child_user, 'under7', 'low')

    db_session.refresh(child_user)
    assignments = ManagedUserBlocklistAssignment.query.filter_by(
        managed_user_id=child_user.id,
    ).all()
    preset_ids = {
        a.source.preset_id
        for a in assignments
        if a.source and a.source.is_marketplace
    }
    assert preset_ids == set(bundle['marketplace_preset_ids'])

    schedule = UserWeeklySchedule.query.filter_by(user_id=child_user.id).first()
    assert schedule is not None
    assert schedule.monday_hours == bundle['weekly_schedule_hours']['weekday']
    assert schedule.saturday_hours == bundle['weekly_schedule_hours']['weekend']


def test_apply_policy_preset_linux_mapping(app, db_session, child_user, linux_mapping):
    with app.app_context():
        apply_policy_preset(child_user, '8_12', 'high')

    policy = MappingLinuxDevicePolicy.query.filter_by(
        device_map_id=linux_mapping.id,
    ).first()
    assert policy is not None
    assert policy.terminal_access_disabled is True
    assert policy.install_software_disabled is True
    assert policy.chrome_policies.get('block_other_extensions') is True

    settings = MappingApprovalSettings.query.filter_by(
        device_map_id=linux_mapping.id,
    ).first()
    assert settings is not None
    assert settings.app_launch_mode == MappingApprovalSettings.APP_LAUNCH_ALLOWLIST
    assert settings.domain_access_mode == MappingApprovalSettings.DOMAIN_APPROVAL_ON_BLOCK
    assert settings.registration_approval_enabled is True


def test_apply_policy_preset_android_device_policy(app, db_session, child_user, android_mapping):
    with app.app_context():
        apply_policy_preset(child_user, '8_12', 'high')

    policy = MappingAndroidDevicePolicy.query.filter_by(
        system_id=android_mapping.system_id,
    ).first()
    assert policy is not None
    assert policy.developer_settings == MappingAndroidDevicePolicy.DEVELOPER_SETTINGS_DISABLED
    assert policy.install_apps_disabled is True
    assert policy.factory_reset_disabled is True
    assert policy.usb_data_access == MappingAndroidDevicePolicy.USB_DATA_ACCESS_DISALLOW_ALL
    assert policy.block_wifi_tethering is True

    db_session.refresh(android_mapping)
    assert android_mapping.android_profile_type == 'restricted'


def test_apply_policy_preset_android_medium_dev_settings(app, db_session, child_user, android_mapping):
    with app.app_context():
        apply_policy_preset(child_user, '13_15', 'medium')

    policy = MappingAndroidDevicePolicy.query.filter_by(
        system_id=android_mapping.system_id,
    ).first()
    assert policy is not None
    assert policy.developer_settings == MappingAndroidDevicePolicy.DEVELOPER_SETTINGS_DISABLED
    assert policy.install_apps_disabled is False
    assert policy.modify_accounts_disabled is True
    assert policy.usb_data_access == MappingAndroidDevicePolicy.USB_DATA_ACCESS_DISALLOW_FILE


def test_apply_policy_preset_android_medium_blocks_bypass_tools(app, db_session, child_user, android_mapping):
    with app.app_context():
        apply_policy_preset(child_user, '8_12', 'medium')

    from src.models import AppPolicy, AppPolicyRule

    bypass_policy = AppPolicy.query.filter(
        AppPolicy.name == f'Anti-bypass tools ({child_user.username})',
        AppPolicy.platform == AppPolicy.PLATFORM_ANDROID,
    ).first()
    assert bypass_policy is not None
    blocked_paths = {rule.executable_path for rule in bypass_policy.rules}
    assert '/android/package/com.oasisfeng.island' in blocked_paths
    assert '/android/package/net.typeblog.shelter' in blocked_paths

    compiled = AppArmorRule.query.filter_by(device_map_id=android_mapping.id).all()
    compiled_paths = {rule.executable_path for rule in compiled}
    assert '/android/package/net.dinglisch.android.taskerm' in compiled_paths


def test_apply_policy_preset_android_low_clears_bypass_tools(app, db_session, child_user, android_mapping):
    with app.app_context():
        apply_policy_preset(child_user, '8_12', 'high')
        apply_policy_preset(child_user, '16_plus', 'low')

    from src.models import AppPolicy

    bypass_policy = AppPolicy.query.filter(
        AppPolicy.name == f'Anti-bypass tools ({child_user.username})',
        AppPolicy.platform == AppPolicy.PLATFORM_ANDROID,
    ).first()
    assert bypass_policy is None


def test_apply_policy_preset_android_low_leaves_defaults(app, db_session, child_user, android_mapping):
    with app.app_context():
        apply_policy_preset(child_user, '16_plus', 'low')

    policy = MappingAndroidDevicePolicy.query.filter_by(
        system_id=android_mapping.system_id,
    ).first()
    assert policy is not None
    assert policy.developer_settings == MappingAndroidDevicePolicy.DEVELOPER_SETTINGS_UNSPECIFIED
    assert policy.install_apps_disabled is False


def test_apply_policy_preset_reapply_overwrites(app, db_session, child_user, linux_mapping):
    with app.app_context():
        apply_policy_preset(child_user, '16_plus', 'low')
    policy = MappingLinuxDevicePolicy.query.filter_by(device_map_id=linux_mapping.id).first()
    assert policy.terminal_access_disabled is False

    with app.app_context():
        apply_policy_preset(child_user, 'under7', 'high')
    db_session.refresh(policy)
    assert policy.terminal_access_disabled is True
    assert child_user.overlay_age_tier == 'under8'


def test_create_managed_user_with_policy_preset(auth_client, db_session):
    response = auth_client.post(
        '/managed-users/add',
        data={
            'username': 'wizard_child',
            'policy_age_bracket': '13_15',
            'policy_maturity_level': 'low',
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    user = ManagedUser.query.filter_by(username='wizard_child').first()
    assert user is not None
    assert user.policy_age_bracket == '13_15'
    assert user.policy_maturity_level == 'low'
    assert user.overlay_age_tier == 'teen'

    marketplace_count = BlocklistSource.query.filter_by(is_marketplace=True).count()
    assert marketplace_count >= 1


def test_apply_policy_preset_route(auth_client, db_session, child_user):
    response = auth_client.post(
        f'/managed-users/{child_user.id}/apply-policy-preset',
        data={
            'policy_age_bracket': '8_12',
            'policy_maturity_level': 'low',
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    db_session.refresh(child_user)
    assert child_user.policy_age_bracket == '8_12'
    assert child_user.policy_maturity_level == 'low'


def test_apply_policy_preset_route_missing_fields(auth_client, child_user):
    response = auth_client.post(
        f'/managed-users/{child_user.id}/apply-policy-preset',
        data={'policy_age_bracket': '8_12'},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b'Age bracket and technical understanding level are required' in response.data
