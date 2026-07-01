"""Broken Object Level Authorization (BOLA) isolation tests for multi-tenant parents."""

from datetime import datetime, timezone

import pytest

from src.models import (
    AgentAlert,
    AgentDevice,
    AppPolicy,
    ApprovalRequest,
    BlocklistSource,
    DeviceScreenshot,
    Household,
    HouseholdParentMembership,
    ManagedUser,
    ManagedUserDeviceMap,
    ParentAccount,
    PolicyApprovalGrant,
    db,
)


@pytest.fixture
def tenant_pair(db_session):
    """Two isolated households with parent, child, device, and mapping each."""
    household_a = Household(name='Household A', enrollment_token='token-a')
    household_b = Household(name='Household B', enrollment_token='token-b')
    db_session.add_all([household_a, household_b])
    db_session.flush()

    parent_a = ParentAccount(email='parent-a@example.com', name='Parent A')
    parent_b = ParentAccount(email='parent-b@example.com', name='Parent B')
    db_session.add_all([parent_a, parent_b])
    db_session.flush()

    db_session.add_all([
        HouseholdParentMembership(household_id=household_a.id, parent_account_id=parent_a.id),
        HouseholdParentMembership(household_id=household_b.id, parent_account_id=parent_b.id),
    ])

    child_a = ManagedUser(
        username='child-a',
        system_ip='Unassigned',
        is_valid=True,
        household_id=household_a.id,
    )
    child_b = ManagedUser(
        username='child-b',
        system_ip='Unassigned',
        is_valid=True,
        household_id=household_b.id,
    )
    db_session.add_all([child_a, child_b])
    db_session.flush()

    device_a = AgentDevice(
        system_id='device-a',
        system_hostname='Device A',
        status='approved',
        platform='linux',
        secure_token='token-a-device',
        household_id=household_a.id,
    )
    device_b = AgentDevice(
        system_id='device-b',
        system_hostname='Device B',
        status='approved',
        platform='linux',
        secure_token='token-b-device',
        household_id=household_b.id,
    )
    pending_b = AgentDevice(
        system_id='pending-b',
        system_hostname='Pending B',
        status='pending',
        platform='linux',
        household_id=household_b.id,
    )
    db_session.add_all([device_a, device_b, pending_b])
    db_session.flush()

    mapping_a = ManagedUserDeviceMap(
        managed_user_id=child_a.id,
        system_id=device_a.system_id,
        linux_username='child-a',
        is_valid=True,
    )
    mapping_b = ManagedUserDeviceMap(
        managed_user_id=child_b.id,
        system_id=device_b.system_id,
        linux_username='child-b',
        is_valid=True,
    )
    db_session.add_all([mapping_a, mapping_b])
    db_session.flush()

    approval_b = ApprovalRequest(
        device_map_id=mapping_b.id,
        request_type=ApprovalRequest.REQUEST_APP_LAUNCH,
        target_kind=ApprovalRequest.TARGET_PACKAGE,
        target_value='com.other.app',
        display_label='Other App',
        status=ApprovalRequest.STATUS_PENDING,
        requested_at=datetime.now(timezone.utc),
    )
    db_session.add(approval_b)
    db_session.flush()

    grant_b = PolicyApprovalGrant(
        device_map_id=mapping_b.id,
        grant_type='app_launch',
        target_kind='package',
        target_value='com.other.app',
        display_label='Other App',
        status=PolicyApprovalGrant.STATUS_ACTIVE,
    )
    db_session.add(grant_b)
    db_session.flush()

    screenshot_b = DeviceScreenshot(
        system_id=device_b.system_id,
        screenshot_id='00000000-0000-4000-8000-0000000000bb',
        linux_username='child-b',
        captured_at=datetime.now(timezone.utc),
        mime_type='image/jpeg',
        content_hash='b' * 64,
        data=b'other-household-screenshot',
    )
    alert_a = AgentAlert(
        system_id=device_a.system_id,
        event_type='test_event',
        linux_username='child-a',
        occurred_at=datetime.now(timezone.utc),
        payload_json='{}',
    )
    alert_b = AgentAlert(
        system_id=device_b.system_id,
        event_type='test_event',
        linux_username='child-b',
        occurred_at=datetime.now(timezone.utc),
        payload_json='{}',
    )
    blocklist_b = BlocklistSource(
        name='household-b-list',
        source_type=BlocklistSource.TYPE_MANUAL,
        is_enabled=True,
        household_id=household_b.id,
    )
    policy_b = AppPolicy(
        name='household-b-policy',
        platform=AppPolicy.PLATFORM_LINUX,
        household_id=household_b.id,
    )
    db_session.add_all([screenshot_b, alert_a, alert_b, blocklist_b, policy_b])
    db_session.commit()

    return {
        'household_a': household_a,
        'household_b': household_b,
        'parent_a': parent_a,
        'parent_b': parent_b,
        'child_a': child_a,
        'child_b': child_b,
        'device_a': device_a,
        'device_b': device_b,
        'pending_b': pending_b,
        'mapping_a': mapping_a,
        'mapping_b': mapping_b,
        'approval_b': approval_b,
        'grant_b': grant_b,
        'screenshot_b': screenshot_b,
        'alert_a': alert_a,
        'alert_b': alert_b,
        'blocklist_b': blocklist_b,
        'policy_b': policy_b,
    }


def _login_parent(client, parent):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['parent_account_id'] = parent.id


# --- Screenshots ---


def test_screenshot_by_id_denied_for_other_household(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.get(f"/api/screenshots/{tenant_pair['screenshot_b'].id}")
    assert response.status_code == 403


def test_screenshot_list_allowed_for_own_device(client, tenant_pair):
    screenshot_a = DeviceScreenshot(
        system_id=tenant_pair['device_a'].system_id,
        screenshot_id='00000000-0000-4000-8000-0000000000aa',
        linux_username='child-a',
        captured_at=datetime.now(timezone.utc),
        mime_type='image/jpeg',
        content_hash='a' * 64,
        data=b'own-household-screenshot',
    )
    db.session.add(screenshot_a)
    db.session.commit()

    _login_parent(client, tenant_pair['parent_a'])
    response = client.get(f"/api/screenshots/{screenshot_a.id}")
    assert response.status_code == 200


# --- Alerts ---


def test_alerts_list_scoped_to_accessible_devices(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.get('/api/alerts')
    assert response.status_code == 200
    payload = response.get_json()
    system_ids = {row['system_id'] for row in payload['data']['alerts']}
    assert tenant_pair['device_a'].system_id in system_ids
    assert tenant_pair['device_b'].system_id not in system_ids


def test_alerts_filter_other_system_id_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.get(f"/api/alerts?system_id={tenant_pair['device_b'].system_id}")
    assert response.status_code == 403


def test_alerts_prune_other_system_id_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.post(
        '/api/alerts/prune',
        json={'older_than_days': 0, 'system_id': tenant_pair['device_b'].system_id},
    )
    assert response.status_code == 403
    assert AgentAlert.query.get(tenant_pair['alert_b'].id) is not None


def test_alerts_prune_own_scope_allowed(client, tenant_pair):
    alert_id = tenant_pair['alert_a'].id
    _login_parent(client, tenant_pair['parent_a'])
    response = client.post(
        '/api/alerts/prune',
        json={'older_than_days': 0, 'system_id': tenant_pair['device_a'].system_id},
    )
    assert response.status_code == 200
    assert AgentAlert.query.get(alert_id) is None


# --- Approvals / grants ---


def test_revoke_grant_other_household_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.post(f"/api/approval-grants/{tenant_pair['grant_b'].id}/revoke")
    assert response.status_code == 403


def test_get_other_household_approval_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.get(f"/api/approvals/{tenant_pair['approval_b'].id}")
    assert response.status_code == 403


def test_approve_other_household_request_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.post(f"/api/approvals/{tenant_pair['approval_b'].id}/approve")
    assert response.status_code == 403


def test_online_accounts_other_child_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.get(f"/api/user/{tenant_pair['child_b'].id}/online-accounts")
    assert response.status_code == 403


# --- Devices ---


def test_pending_devices_scoped_to_household(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.get('/api/devices/pending')
    assert response.status_code == 200
    payload = response.get_json()
    system_ids = {row['system_id'] for row in payload['devices']}
    assert tenant_pair['pending_b'].system_id not in system_ids


def test_pending_devices_visible_to_own_household(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_b'])
    response = client.get('/api/devices/pending')
    assert response.status_code == 200
    payload = response.get_json()
    system_ids = {row['system_id'] for row in payload['devices']}
    assert tenant_pair['pending_b'].system_id in system_ids


def test_approve_other_household_pending_device_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.post(f"/api/device/approve/{tenant_pair['pending_b'].system_id}")
    assert response.status_code == 403


# --- Installed apps ---


def test_installed_apps_other_managed_user_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.get(f"/api/managed-users/{tenant_pair['child_b'].id}/installed-apps")
    assert response.status_code == 403


def test_installed_apps_own_managed_user_allowed(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.get(f"/api/managed-users/{tenant_pair['child_a'].id}/installed-apps")
    assert response.status_code == 200


# --- User mappings / profile creation ---


def test_connect_mapping_other_household_device_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.post(
        f"/api/managed-users/{tenant_pair['child_a'].id}/mappings/connect",
        json={
            'system_id': tenant_pair['device_b'].system_id,
            'linux_username': 'intruder',
        },
    )
    assert response.status_code == 403
    assert ManagedUserDeviceMap.query.filter_by(
        managed_user_id=tenant_pair['child_a'].id,
        system_id=tenant_pair['device_b'].system_id,
    ).count() == 0


def test_create_user_other_household_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.post(
        '/api/user/create',
        json={
            'username': 'sneaky-child',
            'household_id': tenant_pair['household_b'].id,
        },
    )
    assert response.status_code == 403


def test_create_user_own_household_allowed(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.post(
        '/api/user/create',
        json={
            'username': 'allowed-child',
            'household_id': tenant_pair['household_a'].id,
        },
    )
    assert response.status_code == 200
    created = ManagedUser.query.filter_by(username='allowed-child').first()
    assert created is not None
    assert created.household_id == tenant_pair['household_a'].id


# --- Dashboard ---


def test_dashboard_snapshot_excludes_other_household_children(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.get('/api/dashboard')
    assert response.status_code == 200
    payload = response.get_json()
    usernames = {row['username'] for row in payload['users']}
    assert 'child-a' in usernames
    assert 'child-b' not in usernames


# --- Blocklists ---


def test_blocklist_source_delete_other_household_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.post(f"/blocklists/sources/{tenant_pair['blocklist_b'].id}/delete")
    assert response.status_code == 403
    assert BlocklistSource.query.get(tenant_pair['blocklist_b'].id) is not None


def test_blocklist_source_toggle_other_household_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.post(
        f"/blocklists/sources/{tenant_pair['blocklist_b'].id}/toggle",
        data={'is_enabled': 'off'},
    )
    assert response.status_code == 403


# --- App policies ---


def test_app_policy_delete_other_household_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.post(f"/admin/app-policies/{tenant_pair['policy_b'].id}/delete")
    assert response.status_code == 403
    assert AppPolicy.query.get(tenant_pair['policy_b'].id) is not None


# --- Console import ---


def test_xbox_import_assigns_parent_household(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.post(
        '/api/xbox/import-device',
        json={'device_id': 'xbox-console-a', 'name': 'Living Room Xbox'},
    )
    assert response.status_code == 200
    device = AgentDevice.query.get('xbox-console-a')
    assert device is not None
    assert device.household_id == tenant_pair['household_a'].id


def test_nintendo_import_assigns_parent_household(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_b'])
    response = client.post(
        '/api/nintendo/import-device',
        json={'device_id': 'switch-console-b', 'name': 'Family Switch'},
    )
    assert response.status_code == 200
    device = AgentDevice.query.get('switch-console-b')
    assert device is not None
    assert device.household_id == tenant_pair['household_b'].id


# --- Device APIs guarded by path system_id ---


def test_device_screenshots_other_household_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.get(f"/api/devices/{tenant_pair['device_b'].system_id}/screenshots")
    assert response.status_code == 403


def test_user_history_other_child_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.get(f"/api/user/{tenant_pair['child_b'].id}/history")
    assert response.status_code == 403


def test_user_intervals_other_child_denied(client, tenant_pair):
    _login_parent(client, tenant_pair['parent_a'])
    response = client.get(f"/api/user/{tenant_pair['child_b'].id}/intervals")
    assert response.status_code == 403
