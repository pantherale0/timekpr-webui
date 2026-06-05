"""API tests for access approvals."""

from datetime import datetime, timezone

import pytest

from src.database import AgentDevice, ApprovalRequest, ManagedUser, ManagedUserDeviceMap


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


@pytest.fixture
def approval_fixture(db_session):
    device = AgentDevice(system_id='sys-api-approval', status='approved', secure_token='token')
    user = ManagedUser(username='api-child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-api-approval',
        linux_username='api-child',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.flush()
    request_row = ApprovalRequest(
        device_map_id=mapping.id,
        request_type=ApprovalRequest.REQUEST_APP_LAUNCH,
        target_kind=ApprovalRequest.TARGET_PACKAGE,
        target_value='/android/package/com.test.app',
        display_label='Test App',
        status=ApprovalRequest.STATUS_PENDING,
        requested_at=datetime.now(timezone.utc),
    )
    db_session.add(request_row)
    db_session.commit()
    return {'user': user, 'mapping': mapping, 'request': request_row}


def test_list_approvals_requires_auth(client):
    response = client.get('/api/approvals')
    assert response.status_code == 401


def test_list_pending_approvals(auth_client, approval_fixture):
    response = auth_client.get('/api/approvals')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['count'] == 1
    assert payload['approvals'][0]['display_label'] == 'Test App'


def test_approve_request(auth_client, approval_fixture, db_session):
    request_id = approval_fixture['request'].id
    response = auth_client.post(f'/api/approvals/{request_id}/approve')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True

    db_session.refresh(approval_fixture['request'])
    assert approval_fixture['request'].status == ApprovalRequest.STATUS_APPROVED


def test_deny_request(auth_client, approval_fixture, db_session):
    request_id = approval_fixture['request'].id
    response = auth_client.post(
        f'/api/approvals/{request_id}/deny',
        json={'reason': 'No games today'},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True

    db_session.refresh(approval_fixture['request'])
    assert approval_fixture['request'].status == ApprovalRequest.STATUS_DENIED
    assert approval_fixture['request'].denial_reason == 'No games today'


def test_update_mapping_settings(auth_client, approval_fixture):
    mapping_id = approval_fixture['mapping'].id
    response = auth_client.post(
        f'/api/mappings/{mapping_id}/approval-settings',
        json={
            'app_launch_mode': 'allowlist',
            'domain_access_mode': 'approval_on_block',
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['settings']['app_launch_mode'] == 'allowlist'


def test_admin_approvals_page_requires_auth(client):
    response = client.get('/admin/approvals')
    assert response.status_code == 302


def test_admin_approvals_page_renders(auth_client, approval_fixture):
    response = auth_client.get('/admin/approvals')
    assert response.status_code == 200
    assert b'Access Requests' in response.data
    assert b'Test App' in response.data
