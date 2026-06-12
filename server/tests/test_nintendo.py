"""Tests for Nintendo Parental Controls blueprint routes and helpers."""

import json
from unittest.mock import patch, MagicMock
import pytest
from datetime import datetime, timezone, timedelta
from src.database import AgentDevice, Settings
from src.agent_helper import AgentConnectionManager

@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client

def test_login_url_requires_auth(client):
    response = client.get('/api/nintendo/login-url')
    assert response.status_code == 401

def test_login_url_authenticated(auth_client):
    with patch('src.blueprints.api.nintendo.Authenticator') as mock_auth_class:
        mock_auth = MagicMock()
        mock_auth._auth_code_verifier = "test_verifier"
        mock_auth.login_url = "https://mock.login.url"
        mock_auth_class.return_value = mock_auth

        response = auth_client.get('/api/nintendo/login-url')
        assert response.status_code == 200
        data = response.get_json()
        assert data['success'] is True
        assert data['login_url'] == "https://mock.login.url"

        # Check session
        with auth_client.session_transaction() as sess:
            assert sess['nintendo_code_verifier'] == "test_verifier"

def test_authenticate_requires_auth(client):
    response = client.post('/api/nintendo/authenticate', json={'response_url': 'http://localhost/callback'})
    assert response.status_code == 401

@patch('src.blueprints.api.nintendo.run_async')
def test_authenticate_nintendo(mock_async_run, auth_client):
    with auth_client.session_transaction() as sess:
        sess['nintendo_code_verifier'] = 'cached_verifier'

    mock_async_run.return_value = 'mock_session_token'

    response = auth_client.post('/api/nintendo/authenticate', json={'response_url': 'http://localhost/callback'})
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert Settings.get_value('nintendo_session_token') == 'mock_session_token'
    assert Settings.get_value('nintendo_linked_at')

def test_account_status_requires_auth(client):
    response = client.get('/api/nintendo/account-status')
    assert response.status_code == 401

def test_account_status_not_linked(auth_client):
    response = auth_client.get('/api/nintendo/account-status')
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert data['linked'] is False
    assert data['enrolled_device_count'] == 0

def test_account_status_linked(auth_client, db_session):
    Settings.set_value('nintendo_session_token', 'stored_token')
    Settings.set_value('nintendo_linked_at', '2026-06-01T12:00:00+00:00')
    device = AgentDevice(system_id='switch-1', platform='nintendo', status='approved', system_hostname='Living Room')
    db_session.add(device)
    db_session.commit()

    response = auth_client.get('/api/nintendo/account-status')
    assert response.status_code == 200
    data = response.get_json()
    assert data['linked'] is True
    assert data['enrolled_device_count'] == 1
    assert data['enrolled_devices'][0]['system_id'] == 'switch-1'

def test_unlink_requires_auth(client):
    response = client.post('/api/nintendo/unlink')
    assert response.status_code == 401

def test_unlink_nintendo_account(auth_client):
    Settings.set_value('nintendo_session_token', 'stored_token')
    Settings.set_value('nintendo_linked_at', '2026-06-01T12:00:00+00:00')

    response = auth_client.post('/api/nintendo/unlink')
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert Settings.get_value('nintendo_session_token') == ''
    assert Settings.get_value('nintendo_linked_at') == ''

def test_list_devices_requires_auth(client):
    response = client.get('/api/nintendo/devices')
    assert response.status_code == 401

@patch('src.blueprints.api.nintendo.run_async')
def test_list_nintendo_devices(mock_async_run, auth_client):
    Settings.set_value('nintendo_session_token', 'stored_token')

    # Return structure matching what _list_devices_async returns
    mock_async_run.return_value = [{
        'device_id': 'd1',
        'name': 'Switch Console',
        'model': 'OLED',
        'limit_time': 60,
        'today_playing_time': 45,
        'players': [{
            'player_id': 'p1',
            'nickname': 'MiiNickname',
            'player_image': 'http://image.url',
            'playing_time': 30
        }]
    }]

    response = auth_client.get('/api/nintendo/devices')
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert len(data['devices']) == 1
    assert data['devices'][0]['device_id'] == 'd1'
    assert data['devices'][0]['players'][0]['nickname'] == 'MiiNickname'

def test_import_device_requires_auth(client):
    response = client.post('/api/nintendo/import-device', json={'device_id': 'd1'})
    assert response.status_code == 401

def test_import_device(auth_client, db_session):
    response = auth_client.post('/api/nintendo/import-device', json={'device_id': 'new_switch', 'name': 'Living Room Switch'})
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True

    # Assert database state
    dev = AgentDevice.query.get('new_switch')
    assert dev is not None
    assert dev.platform == 'nintendo'
    assert dev.system_hostname == 'Living Room Switch'
    assert dev.status == 'approved'

def test_agent_connection_manager_online_status(db_session):
    # Setup approved nintendo device
    device = AgentDevice(system_id='switch_online', platform='nintendo', status='approved')
    db_session.add(device)
    db_session.commit()

    # Mapped account config with recent timestamp
    from src.database import ManagedUserDeviceMap, ManagedUser, db
    user = ManagedUser(username='test_child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.commit()

    # Case 1: active playtime change within 10 minutes -> online
    recent_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='switch_online',
        linux_username='player_1',
        last_config=json.dumps({'last_playtime_change_at': recent_time.isoformat()})
    )
    db_session.add(mapping)
    db_session.commit()

    assert AgentConnectionManager.is_online('switch_online') is True

    # Case 2: active playtime change > 10 minutes -> offline
    old_time = datetime.now(timezone.utc) - timedelta(minutes=15)
    mapping.last_config = json.dumps({'last_playtime_change_at': old_time.isoformat()})
    db_session.commit()

    assert AgentConnectionManager.is_online('switch_online') is False


def test_mapping_display_linux_username_uses_nintendo_nickname(db_session):
    import json
    from src.database import ManagedUser, ManagedUserDeviceMap

    device = AgentDevice(
        system_id='switch-nick',
        platform='nintendo',
        status='approved',
        linux_users_json=json.dumps([
            {
                'username': 'player-uuid-1',
                'uid': 0,
                'nickname': 'MiiNickname',
                'player_image': 'http://example.com/mii.png',
            }
        ]),
    )
    user = ManagedUser(username='child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.commit()

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='switch-nick',
        linux_username='player-uuid-1',
    )
    db_session.add(mapping)
    db_session.commit()

    assert mapping.display_linux_username == 'MiiNickname'
    assert mapping.nintendo_player['nickname'] == 'MiiNickname'
