"""Unit tests for approvals_manager."""

from datetime import datetime, timezone

import pytest

from src.blocklist_helper import compute_mapping_policy_hash
from src.approvals_manager import (
    approve_request,
    build_app_approval_sync_extras,
    build_domain_allowed_domains,
    compute_approval_revision_hash,
    create_grant,
    deny_request,
    ingest_access_request,
    revoke_grant,
    upsert_settings,
)
from src.database import (
    AgentDevice,
    AppArmorRule,
    ApprovalRequest,
    DeviceInstalledApplication,
    ManagedUser,
    ManagedUserDeviceMap,
    MappingApprovalSettings,
    PolicyApprovalGrant,
)
from src.installed_apps_manager import ANDROID_PACKAGE_PREFIX


@pytest.fixture
def approval_mapping(db_session):
    device = AgentDevice(system_id='sys-approval', status='approved', secure_token='token')
    user = ManagedUser(username='child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-approval',
        linux_username='child',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()
    return mapping


def test_ingest_app_blocked_not_approved_fallback(approval_mapping, db_session):
    alert = {
        'event_type': 'app_blocked',
        'linux_username': 'child',
        'details': {
            'reason': 'not_approved',
            'executable_path': '/android/package/com.blocked.app',
            'application_name': 'Blocked App',
        },
    }
    row = ingest_access_request('sys-approval', alert, source_alert_id=None)
    assert row is not None
    assert row.request_type == ApprovalRequest.REQUEST_APP_LAUNCH


def test_ingest_dedupes_pending_requests(approval_mapping, db_session):
    alert = {
        'event_type': 'access_requested',
        'linux_username': 'child',
        'details': {
            'request_type': 'app_launch',
            'target_kind': 'package',
            'target_value': 'com.example.game',
            'display_label': 'Example Game',
        },
    }
    first = ingest_access_request('sys-approval', alert, source_alert_id=None)
    second = ingest_access_request('sys-approval', alert, source_alert_id=None)

    assert first.id == second.id
    assert ApprovalRequest.query.filter_by(status=ApprovalRequest.STATUS_PENDING).count() == 1


def test_approve_creates_grant(approval_mapping, db_session):
    request_row = ApprovalRequest(
        device_map_id=approval_mapping.id,
        request_type=ApprovalRequest.REQUEST_APP_LAUNCH,
        target_kind=ApprovalRequest.TARGET_PACKAGE,
        target_value='/android/package/com.example.game',
        display_label='Example Game',
        status=ApprovalRequest.STATUS_PENDING,
        requested_at=datetime.now(timezone.utc),
    )
    db_session.add(request_row)
    db_session.commit()

    approve_request(request_row.id, decided_by='admin')

    grant = PolicyApprovalGrant.query.filter_by(
        device_map_id=approval_mapping.id,
        status=PolicyApprovalGrant.STATUS_ACTIVE,
    ).first()
    assert grant is not None
    assert grant.target_value == '/android/package/com.example.game'
    assert request_row.status == ApprovalRequest.STATUS_APPROVED


def test_deny_does_not_create_grant(approval_mapping, db_session):
    request_row = ApprovalRequest(
        device_map_id=approval_mapping.id,
        request_type=ApprovalRequest.REQUEST_DOMAIN_ACCESS,
        target_kind=ApprovalRequest.TARGET_DOMAIN,
        target_value='example.com',
        display_label='example.com',
        status=ApprovalRequest.STATUS_PENDING,
        requested_at=datetime.now(timezone.utc),
    )
    db_session.add(request_row)
    db_session.commit()

    deny_request(request_row.id, decided_by='admin', reason='Not allowed')

    assert PolicyApprovalGrant.query.count() == 0
    assert request_row.status == ApprovalRequest.STATUS_DENIED


def test_revoke_removes_active_grant(approval_mapping, db_session):
    grant = create_grant(
        approval_mapping,
        grant_type=PolicyApprovalGrant.GRANT_APP_LAUNCH,
        target_kind=PolicyApprovalGrant.TARGET_PACKAGE,
        target_value='com.example.app',
        display_label='Example App',
        created_by='admin',
    )
    revoke_grant(grant.id, revoked_by='admin')
    db_session.refresh(grant)
    assert grant.status == PolicyApprovalGrant.STATUS_REVOKED


def test_allowlist_payload_includes_blocked_packages(approval_mapping, db_session):
    upsert_settings(approval_mapping, app_launch_mode='allowlist')
    extras = build_app_approval_sync_extras(approval_mapping)
    assert extras is not None
    assert extras['app_launch_mode'] == 'allowlist'
    assert isinstance(extras['approved_packages'], list)
    assert isinstance(extras['blocked_packages'], list)


def test_allowlist_treats_app_policy_allowed_as_approved(approval_mapping, db_session):
    device = approval_mapping.device
    device.platform = 'android'
    upsert_settings(approval_mapping, app_launch_mode='allowlist')

    chrome_id = f'{ANDROID_PACKAGE_PREFIX}com.android.chrome'
    db_session.add_all([
        AppArmorRule(
            device_map_id=approval_mapping.id,
            application_name='Chrome',
            executable_path=chrome_id,
            match_type=AppArmorRule.MATCH_TYPE_PACKAGE,
            preset=AppArmorRule.PRESET_ALLOWED,
        ),
        DeviceInstalledApplication(
            system_id=approval_mapping.system_id,
            linux_username=approval_mapping.linux_username,
            application_name='Chrome',
            identifier=chrome_id,
            match_type='package',
            platform='android',
            is_present=True,
        ),
        DeviceInstalledApplication(
            system_id=approval_mapping.system_id,
            linux_username=approval_mapping.linux_username,
            application_name='Calculator',
            identifier=f'{ANDROID_PACKAGE_PREFIX}com.android.calculator2',
            match_type='package',
            platform='android',
            is_present=True,
        ),
    ])
    db_session.commit()

    extras = build_app_approval_sync_extras(approval_mapping)
    assert 'com.android.chrome' in extras['approved_packages']
    assert 'com.android.chrome' not in extras['blocked_packages']
    assert 'com.android.calculator2' in extras['blocked_packages']


def test_domain_allowed_domains_requires_mode(approval_mapping, db_session):
    create_grant(
        approval_mapping,
        grant_type=PolicyApprovalGrant.GRANT_DOMAIN_ACCESS,
        target_kind=PolicyApprovalGrant.TARGET_DOMAIN,
        target_value='wikipedia.org',
        display_label='Wikipedia',
        created_by='admin',
    )
    assert build_domain_allowed_domains(approval_mapping) == []

    upsert_settings(approval_mapping, domain_access_mode='approval_on_block')
    assert build_domain_allowed_domains(approval_mapping) == ['wikipedia.org']


def test_approval_revision_hash_changes_with_grant(approval_mapping, db_session):
    before = compute_approval_revision_hash(approval_mapping)
    create_grant(
        approval_mapping,
        grant_type=PolicyApprovalGrant.GRANT_APP_LAUNCH,
        target_kind=PolicyApprovalGrant.TARGET_PACKAGE,
        target_value='com.new.app',
        display_label='New App',
        created_by='admin',
    )
    after = compute_approval_revision_hash(approval_mapping)
    assert before != after


@pytest.fixture
def linux_approval_mapping(db_session):
    device = AgentDevice(
        system_id='sys-linux-approval',
        status='approved',
        secure_token='token',
        platform='linux',
    )
    user = ManagedUser(username='linuxchild', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-linux-approval',
        linux_username='linuxchild',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()
    return mapping


def test_linux_executable_grant_and_ingest(linux_approval_mapping, db_session):
    grant = create_grant(
        linux_approval_mapping,
        grant_type=PolicyApprovalGrant.GRANT_APP_LAUNCH,
        target_kind=PolicyApprovalGrant.TARGET_EXECUTABLE,
        target_value='/usr/bin/steam',
        display_label='Steam',
        created_by='admin',
    )
    assert grant.target_value == '/usr/bin/steam'
    assert grant.target_kind == PolicyApprovalGrant.TARGET_EXECUTABLE

    alert = {
        'event_type': 'access_requested',
        'linux_username': 'linuxchild',
        'details': {
            'request_type': 'app_launch',
            'target_kind': 'executable',
            'target_value': '/usr/bin/firefox',
            'display_label': 'Firefox',
        },
    }
    row = ingest_access_request('sys-linux-approval', alert, source_alert_id=None)
    assert row is not None
    assert row.target_kind == ApprovalRequest.TARGET_EXECUTABLE
    assert row.target_value == '/usr/bin/firefox'


def test_linux_app_blocked_not_approved_ingest(linux_approval_mapping, db_session):
    alert = {
        'event_type': 'app_blocked',
        'linux_username': 'linuxchild',
        'details': {
            'reason': 'not_approved',
            'executable_path': '/usr/bin/steam',
            'application_name': 'Steam',
            'target_kind': 'executable',
        },
    }
    row = ingest_access_request('sys-linux-approval', alert, source_alert_id=None)
    assert row is not None
    assert row.target_kind == ApprovalRequest.TARGET_EXECUTABLE
    assert row.target_value == '/usr/bin/steam'


def test_linux_allowlist_payload_with_executable_paths(linux_approval_mapping, db_session):
    upsert_settings(linux_approval_mapping, app_launch_mode='allowlist')
    db_session.add_all([
        AppArmorRule(
            device_map_id=linux_approval_mapping.id,
            application_name='Firefox',
            executable_path='/usr/bin/firefox',
            match_type=AppArmorRule.MATCH_TYPE_EXECUTABLE,
            preset=AppArmorRule.PRESET_ALLOWED,
        ),
        DeviceInstalledApplication(
            system_id=linux_approval_mapping.system_id,
            linux_username=linux_approval_mapping.linux_username,
            application_name='Firefox',
            identifier='/usr/bin/firefox',
            match_type='executable',
            platform='linux',
            is_present=True,
        ),
        DeviceInstalledApplication(
            system_id=linux_approval_mapping.system_id,
            linux_username=linux_approval_mapping.linux_username,
            application_name='Steam',
            identifier='/usr/bin/steam',
            match_type='executable',
            platform='linux',
            is_present=True,
        ),
    ])
    db_session.commit()

    extras = build_app_approval_sync_extras(linux_approval_mapping)
    assert '/usr/bin/firefox' in extras['approved_packages']
    assert '/usr/bin/firefox' not in extras['blocked_packages']
    assert '/usr/bin/steam' in extras['blocked_packages']


def test_policy_hash_includes_approval_revision(approval_mapping, db_session):
    source_state = {'1': {'revision': 'abc', 'domain_count': 1}}
    before = compute_mapping_policy_hash(1000, source_state, [1])
    create_grant(
        approval_mapping,
        grant_type=PolicyApprovalGrant.GRANT_DOMAIN_ACCESS,
        target_kind=PolicyApprovalGrant.TARGET_DOMAIN,
        target_value='news.example.com',
        display_label='News',
        created_by='admin',
    )
    revision = compute_approval_revision_hash(approval_mapping)
    after = compute_mapping_policy_hash(1000, source_state, [1], approval_revision=revision)
    assert before != after
