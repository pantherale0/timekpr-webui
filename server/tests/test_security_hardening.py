"""Security hardening regression tests."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.models import (
    AgentDevice,
    Household,
    HouseholdParentMembership,
    ManagedUser,
    ManagedUserDeviceMap,
    ManagedUserShare,
    ParentAccount,
    db,
)


@pytest.fixture
def secured_tenant(db_session):
    household = Household(name='Secure Home', enrollment_token='house-enroll-token')
    db_session.add(household)
    db_session.flush()

    owner = ParentAccount(email='owner@example.com', name='Owner')
    viewer = ParentAccount(email='viewer@example.com', name='Viewer')
    db_session.add_all([owner, viewer])
    db_session.flush()

    db_session.add(HouseholdParentMembership(
        household_id=household.id,
        parent_account_id=owner.id,
        permissions_json={'is_owner': True},
    ))
    db_session.add(HouseholdParentMembership(
        household_id=household.id,
        parent_account_id=viewer.id,
        permissions_json={'can_view_screentime': True},
    ))

    child = ManagedUser(
        username='secure-child',
        system_ip='Unassigned',
        is_valid=True,
        household_id=household.id,
    )
    db_session.add(child)
    db_session.flush()

    device = AgentDevice(
        system_id='secure-device',
        status='approved',
        platform='linux',
        secure_token='device-bearer-token',
        household_id=household.id,
        windows_local_admin_password_escrow='encrypted',
    )
    db_session.add(device)
    db_session.flush()

    mapping = ManagedUserDeviceMap(
        managed_user_id=child.id,
        system_id=device.system_id,
        linux_username='secure-child',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()

    return {
        'household': household,
        'owner': owner,
        'viewer': viewer,
        'child': child,
        'device': device,
        'mapping': mapping,
    }


def _login(client, parent):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['parent_account_id'] = parent.id


def test_access_request_requires_bearer_token(client, secured_tenant):
    response = client.post(
        '/api/access-request',
        json={
            'system_id': secured_tenant['device'].system_id,
            'linux_username': 'secure-child',
            'reason': 'filtered',
            'message': 'please',
        },
    )
    assert response.status_code == 401


def test_access_request_rejects_foreign_system_id(client, secured_tenant):
    other = AgentDevice(system_id='other-device', status='approved', secure_token='other-token')
    db.session.add(other)
    db.session.commit()

    response = client.post(
        '/api/access-request',
        json={
            'system_id': other.system_id,
            'linux_username': 'secure-child',
            'reason': 'filtered',
            'message': 'please',
        },
        headers={'Authorization': 'Bearer device-bearer-token'},
    )
    assert response.status_code == 403


def test_access_request_accepts_authenticated_agent(client, secured_tenant):
    response = client.post(
        '/api/access-request',
        json={
            'system_id': secured_tenant['device'].system_id,
            'linux_username': 'secure-child',
            'reason': 'filtered',
            'message': 'please',
        },
        headers={'Authorization': 'Bearer device-bearer-token'},
    )
    assert response.status_code == 201


def test_view_only_parent_cannot_reveal_windows_laps_password(client, secured_tenant):
    _login(client, secured_tenant['viewer'])
    response = client.post(
        f"/api/devices/{secured_tenant['device'].system_id}/windows-laps/reveal-password",
        json={},
    )
    assert response.status_code == 403


def test_owner_can_reveal_windows_laps_password(client, secured_tenant, monkeypatch):
    monkeypatch.setattr(
        'src.device.windows_laps.decrypt_setting',
        lambda _value: 'secret-password',
    )
    secured_tenant['device'].windows_local_admin_password_escrow = 'encrypted'
    db.session.commit()

    _login(client, secured_tenant['owner'])
    response = client.post(
        f"/api/devices/{secured_tenant['device'].system_id}/windows-laps/reveal-password",
        json={},
    )
    assert response.status_code == 200


def test_provisioning_apk_requires_login(client):
    response = client.get('/api/pairing/provisioning/apk')
    assert response.status_code == 401


def test_safe_requests_get_blocks_redirect_to_private_host():
    from src.common.url_safety import safe_requests_get

    public_response = MagicMock()
    public_response.status_code = 302
    public_response.headers = {'Location': 'http://127.0.0.1/internal'}

    with patch('requests.get', return_value=public_response):
        with patch('src.common.url_safety.validate_safe_outbound_url', side_effect=[
            'https://public.example/start',
            ValueError('blocked'),
        ]):
            with pytest.raises(ValueError, match='blocked'):
                safe_requests_get('https://public.example/start')


def test_blocklist_refresh_uses_safe_requests_get(app, db_session):
    from src.models import BlocklistSource
    from src.common.tasks import BackgroundTaskManager

    source = BlocklistSource(
        name='redirect-test',
        source_type=BlocklistSource.TYPE_EXTERNAL_URL,
        source_url='https://public.example/list.txt',
        is_enabled=True,
    )
    db.session.add(source)
    db.session.commit()

    response = MagicMock()
    response.status_code = 200
    response.iter_content = lambda chunk_size=65536: [b'example.com\n']
    response.headers = {}
    response.encoding = 'utf-8'
    response.close = MagicMock()

    manager = BackgroundTaskManager()
    with app.app_context(), \
         patch('src.common.url_safety.is_safe_outbound_url', return_value=True), \
         patch('src.common.url_safety.safe_requests_get', return_value=response) as mock_get:
        manager.refresh_external_blocklist_source(source.id, force=True)
        mock_get.assert_called_once()


def test_oidc_allow_any_rejected_outside_testing(monkeypatch):
    from src.common.oidc import OIDCHelper

    monkeypatch.delenv('TESTING', raising=False)
    monkeypatch.setenv('OIDC_ALLOW_ANY_AUTHENTICATED', 'true')
    helper = OIDCHelper()
    helper.enabled = True
    allowed, _message = helper.is_authorized_admin({'email': 'anyone@example.com'})
    assert allowed is False
