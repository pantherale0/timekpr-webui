import json
import pytest
import hmac
import hashlib
from datetime import datetime
from unittest.mock import patch, MagicMock
from app import ws_agent_handler
from src.database import AgentDevice, ManagedUser, Settings, UserDailyTimeInterval, UserWeeklySchedule, db
from src.agent_helper import AgentConnectionManager

def test_login_routes(client, db_session):
    # 1. GET login page when not logged in
    res = client.get('/')
    assert res.status_code == 200
    assert b"Login" in res.data

    # 2. POST login with invalid password
    res = client.post('/', data={'username': 'admin', 'password': 'wrongpassword'}, follow_redirects=True)
    assert b"Invalid credentials" in res.data

    # 3. POST login with correct password
    Settings.set_admin_password("admin")
    res = client.post('/', data={'username': 'admin', 'password': 'admin'}, follow_redirects=True)
    assert b"Dashboard" in res.data

    # 4. GET login page when already logged in -> redirects to dashboard
    res = client.get('/')
    assert res.status_code == 302
    assert "/dashboard" in res.headers['Location']

    # 5. Logout route
    res = client.get('/logout', follow_redirects=True)
    assert b"logged out" in res.data

def test_oidc_login_redirect(client, db_session):
    with patch('src.oidc_helper.OIDCHelper.is_enabled', new=True), \
         patch('src.oidc_helper.OIDCHelper.get_authorization_url') as mock_auth_url:
        mock_auth_url.return_value = "https://auth.example.com/login?state=123"
        
        res = client.get('/', follow_redirects=False)
        assert res.status_code == 302
        assert "https://auth.example.com/login" in res.headers['Location']

def test_oidc_callback_route(client, db_session):
    with patch('src.oidc_helper.OIDCHelper.is_enabled', new=True):
        # Simulate OIDC Callback with missing code
        with client.session_transaction() as sess:
            sess['oidc_state'] = 'state123'
        res = client.get('/callback?state=state123', follow_redirects=True)
        assert b"No authorization code returned" in res.data

        # Simulate OIDC Callback with state mismatch
        res = client.get('/callback?state=wrongstate&code=code123', follow_redirects=True)
        assert b"Invalid state token" in res.data

        # Simulate OIDC Callback success
        with client.session_transaction() as sess:
            sess['oidc_state'] = 'state123'
        
        with patch('src.oidc_helper.OIDCHelper.exchange_code') as mock_exchange, \
             patch('src.oidc_helper.OIDCHelper.get_user_info') as mock_userinfo:
            mock_exchange.return_value = {'access_token': 'access-token'}
            mock_userinfo.return_value = {'preferred_username': 'oidc-admin', 'email': 'admin@oidc.com'}

            res = client.get('/callback?state=state123&code=code123', follow_redirects=True)
            assert b"Dashboard" in res.data
            # Clean session
            client.get('/logout')

def test_dashboard_routes(client, db_session):
    # Try accessing when not logged in
    res = client.get('/dashboard')
    assert res.status_code == 302

    # Log in
    Settings.set_admin_password("admin")
    client.post('/', data={'username': 'admin', 'password': 'admin'})

    # Create dummy users for dashboard listing
    device = AgentDevice(system_id="sys-1", status="approved", secure_token="token")
    user = ManagedUser(username="jack", system_id="sys-1", system_ip="10.0.0.9", is_valid=True)
    db_session.add_all([device, user])
    db_session.commit()

    res = client.get('/dashboard')
    assert res.status_code == 200
    assert b"jack" in res.data

def test_admin_panel(client, db_session):
    Settings.set_admin_password("admin")
    client.post('/', data={'username': 'admin', 'password': 'admin'})

    res = client.get('/admin')
    assert res.status_code == 200
    assert b"Admin Panel" in res.data

def test_settings_page(client, db_session):
    Settings.set_admin_password("admin")
    client.post('/', data={'username': 'admin', 'password': 'admin'})

    # GET Settings
    res = client.get('/settings')
    assert res.status_code == 200

    # POST Change Password - Empty fields
    res = client.post('/settings', data={'current_password': '', 'new_password': '', 'confirm_password': ''}, follow_redirects=True)
    assert b"All fields are required" in res.data

    # POST Change Password - Wrong current password
    res = client.post('/settings', data={'current_password': 'wrong', 'new_password': 'pass', 'confirm_password': 'pass'}, follow_redirects=True)
    assert b"Current password is incorrect" in res.data

    # POST Change Password - Passwords mismatch
    res = client.post('/settings', data={'current_password': 'admin', 'new_password': 'pass1', 'confirm_password': 'pass2'}, follow_redirects=True)
    assert b"Passwords do not match" in res.data

    # POST Change Password - Too short
    res = client.post('/settings', data={'current_password': 'admin', 'new_password': 'pw', 'confirm_password': 'pw'}, follow_redirects=True)
    assert b"must be at least 4 characters" in res.data

    # POST Change Password - Success
    res = client.post('/settings', data={'current_password': 'admin', 'new_password': 'newadmin', 'confirm_password': 'newadmin'}, follow_redirects=True)
    assert b"updated successfully" in res.data

def test_user_operations(client, db_session):
    Settings.set_admin_password("admin")
    client.post('/', data={'username': 'admin', 'password': 'admin'})

    # Approve a device so we can register a user to it
    device = AgentDevice(system_id="device-abc", status="approved", secure_token="tkn")
    db_session.add(device)
    db_session.commit()

    # 1. Add new user - missing username/system_id
    res = client.post('/users/add', data={'username': '', 'system_id': ''}, follow_redirects=True)
    assert b"Both username and system ID are required" in res.data

    # 2. Add new user - device not approved
    res = client.post('/users/add', data={'username': 'bob', 'system_id': 'device-unapproved'}, follow_redirects=True)
    assert b"is not registered or approved" in res.data

    # 3. Add new user - success (validated user mock)
    with patch('src.agent_helper.AgentClient.validate_user') as mock_val:
        mock_val.return_value = (True, "Valid User", {"TIME_SPENT_DAY": 600})
        res = client.post('/users/add', data={'username': 'bob', 'system_id': 'device-abc'}, follow_redirects=True)
        assert b"added and validated successfully" in res.data

    # 4. Add existing user
    res = client.post('/users/add', data={'username': 'bob', 'system_id': 'device-abc'}, follow_redirects=True)
    assert b"already exists" in res.data

    # Retrieve bob's user record
    bob_user = ManagedUser.query.filter_by(username="bob").first()
    assert bob_user is not None

    # 5. Validate user manual triggers
    with patch('src.agent_helper.AgentClient.validate_user') as mock_val:
        mock_val.return_value = (True, "Valid User", {"TIME_SPENT_DAY": 1200})
        res = client.get(f'/users/validate/{bob_user.id}', follow_redirects=True)
        assert b"validated successfully" in res.data

    # 6. Delete user
    res = client.post(f'/users/delete/{bob_user.id}', follow_redirects=True)
    assert b"removed successfully" in res.data

def test_rest_apis(client, db_session):
    Settings.set_admin_password("admin")
    client.post('/', data={'username': 'admin', 'password': 'admin'})

    # Setup devices
    device_pending = AgentDevice(system_id="sys-pending", status="pending")
    device_approved = AgentDevice(system_id="sys-approved", status="approved", secure_token="some-tkn")
    db_session.add_all([device_pending, device_approved])
    db_session.commit()

    # Approve Device API - Device Not Found
    res = client.post('/api/device/approve/sys-none')
    assert res.status_code == 404

    # Approve Device API - Device not pending
    res = client.post('/api/device/approve/sys-approved')
    assert res.status_code == 400

    # Approve Device API - Success
    res = client.post('/api/device/approve/sys-pending')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['success']
    assert device_pending.status == "approved"
    assert device_pending.secure_token is not None

    # Reject Device API - Device Not Found
    res = client.post('/api/device/reject/sys-none')
    assert res.status_code == 404

    # Reject Device API - Success
    res = client.post('/api/device/reject/sys-approved')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['success']
    assert device_approved.status == "rejected"
    assert device_approved.secure_token is None

    # Task status and restart APIs
    res = client.get('/api/task-status')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert 'running' in data['status']

    res = client.get('/restart-tasks', follow_redirects=True)
    assert b"restarted" in res.data

def test_websocket_handler(app, db_session):
    class MockWS:
        def __init__(self, messages):
            self.messages = messages
            self.sent_messages = []
            self.closed = False
            self.timeout = None

        def receive(self, timeout=None):
            self.timeout = timeout
            if self.messages:
                return self.messages.pop(0)
            return None

        def send(self, data):
            self.sent_messages.append(data)

        def close(self):
            self.closed = True

    # 1. Connection attempt timeout (empty hello)
    ws_timeout = MockWS([])
    with app.test_request_context('/ws', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        ws_agent_handler(ws_timeout)
    assert not ws_timeout.sent_messages

    # 2. Hello with invalid type
    ws_invalid = MockWS([json.dumps({"type": "not_hello"})])
    with app.test_request_context('/ws', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        ws_agent_handler(ws_invalid)
    assert json.loads(ws_invalid.sent_messages[0])['message'] == "Expected 'hello' type"

    # 3. Hello missing system_id
    ws_missing = MockWS([json.dumps({"type": "hello"})])
    with app.test_request_context('/ws', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        ws_agent_handler(ws_missing)
    assert json.loads(ws_missing.sent_messages[0])['message'] == "Missing system_id"

    # 4. Hello invalid registration token firewall check
    with patch('src.agent_helper.REGISTRATION_TOKEN', new='secret-firewall-token'):
        ws_firewall = MockWS([json.dumps({
            "type": "hello",
            "system_id": "sys-fw",
            "registration_token": "wrong-token"
        })])
        with app.test_request_context('/ws', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            ws_agent_handler(ws_firewall)
        assert json.loads(ws_firewall.sent_messages[0])['message'] == "Invalid registration token"

    # 5. New Pending Device pair registration
    ws_pending_new = MockWS([json.dumps({
        "type": "hello",
        "system_id": "sys-new-pending"
    })])
    with app.test_request_context('/ws', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        ws_agent_handler(ws_pending_new)
    # Check that it gets marked pending and pairing_status sent
    assert json.loads(ws_pending_new.sent_messages[0])['type'] == "pairing_status"
    assert json.loads(ws_pending_new.sent_messages[0])['status'] == "pending"

    # 6. Approved Device authentication flow (HMAC challenge-response)
    system_id = "approved-system-id"
    token = "approved-token"
    device = AgentDevice(system_id=system_id, status="approved", secure_token=token)
    db_session.add(device)
    db_session.commit()

    # Step A: Hello
    hello_msg = json.dumps({"type": "hello", "system_id": system_id})
    
    # Custom MockWS that captures the challenge sent by the handler and generates a valid signature response
    # to feed back on next receive() call
    class FlowWS(MockWS):
        def send(self, data):
            super().send(data)
            payload = json.loads(data)
            if payload.get("type") == "challenge":
                challenge = payload.get("challenge")
                # Calculate signature
                token_bytes = token.encode('utf-8')
                msg = (challenge + system_id).encode('utf-8')
                signature = hmac.new(token_bytes, msg, hashlib.sha256).hexdigest()
                # Queue signature response for next receive call
                self.messages.append(json.dumps({
                    "type": "register",
                    "signature": signature
                }))

    ws_flow = FlowWS([hello_msg])
    with app.test_request_context('/ws', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        ws_agent_handler(ws_flow)

    # Check that challenge was sent and auth_result was successful
    assert len(ws_flow.sent_messages) == 2
    assert json.loads(ws_flow.sent_messages[0])['type'] == "challenge"
    assert json.loads(ws_flow.sent_messages[1])['type'] == "auth_result"
    assert json.loads(ws_flow.sent_messages[1])['success'] is True

def test_new_endpoints(client, db_session):
    Settings.set_admin_password("admin")
    client.post('/', data={'username': 'admin', 'password': 'admin'})

    device = AgentDevice(system_id="sys-new", status="approved", secure_token="tkn")
    user = ManagedUser(username="jack", system_id="sys-new", system_ip="127.0.0.1", is_valid=True)
    db_session.add_all([device, user])
    db_session.commit()

    res = client.get(f'/stats/{user.id}')
    assert res.status_code == 200
    assert b"jack" in res.data

    res = client.post('/weekly-schedule/update', data={
        'user_id': user.id,
        'monday': '2.5',
        'tuesday': '3.0'
    }, follow_redirects=True)
    assert res.status_code == 200
    assert user.weekly_schedule is not None
    assert user.weekly_schedule.monday_hours == 2.5
    assert user.weekly_schedule.tuesday_hours == 3.0

    res = client.post('/weekly-schedule/update', data={'monday': '2.5'}, follow_redirects=True)
    assert b"User ID is required" in res.data

    res = client.post('/weekly-schedule/update', data={'user_id': 'invalid'}, follow_redirects=True)
    assert b"Invalid user ID" in res.data

    res = client.get(f'/api/schedule-sync-status/{user.id}')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['success']
    assert not data['is_synced']

    user_no_sched = ManagedUser(username="nosched", system_id="sys-new", system_ip="127.0.0.1")
    db_session.add(user_no_sched)
    db_session.commit()
    res = client.get(f'/api/schedule-sync-status/{user_no_sched.id}')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['is_synced']
    assert data['schedule'] is None

    res = client.get(f'/api/user/{user.id}/intervals')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['success']
    assert data['username'] == "jack"

    interval_data = {
        'intervals': {
            '1': {
                'start_hour': 9,
                'start_minute': 0,
                'end_hour': 17,
                'end_minute': 0,
                'is_enabled': True
            }
        }
    }
    res = client.post(
        f'/api/user/{user.id}/intervals/update',
        data=json.dumps(interval_data),
        content_type='application/json'
    )
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['success']

    interval = UserDailyTimeInterval.query.filter_by(user_id=user.id, day_of_week=1).first()
    assert interval is not None
    assert interval.start_hour == 9
    assert not interval.is_synced

    invalid_interval_data = {
        'intervals': {
            '1': {
                'start_hour': 18,
                'start_minute': 0,
                'end_hour': 17,
                'end_minute': 0,
                'is_enabled': True
            }
        }
    }
    res = client.post(
        f'/api/user/{user.id}/intervals/update',
        data=json.dumps(invalid_interval_data),
        content_type='application/json'
    )
    assert res.status_code == 400

    res = client.post(
        f'/api/user/{user.id}/intervals/update',
        data='null',
        content_type='application/json'
    )
    assert res.status_code == 400

    res = client.get(f'/api/user/{user.id}/intervals/sync-status')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['success']
    assert data['needs_sync']

    class DummyWS:
        def send(self, message):
            pass

    ws = DummyWS()
    AgentConnectionManager.register("sys-new", ws, "127.0.0.1")
    
    with patch('src.agent_helper.AgentClient.modify_time_left') as mock_modify, \
         patch('src.agent_helper.AgentClient.validate_user') as mock_val:
        mock_modify.return_value = (True, "Time modified successfully")
        mock_val.return_value = (True, "Valid User", {"TIME_SPENT_DAY": 1200})
        res = client.post('/api/modify-time', data={
            'user_id': user.id,
            'operation': '+',
            'seconds': '300'
        })
        assert res.status_code == 200
        data = json.loads(res.data)
        assert data['success']
        assert not data.get('pending')

    AgentConnectionManager.unregister("sys-new")
    res = client.post('/api/modify-time', data={
        'user_id': user.id,
        'operation': '-',
        'seconds': '600'
    })
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['success']
    assert data['pending']
    assert user.pending_time_adjustment == 600
    assert user.pending_time_operation == '-'

    res = client.post('/api/modify-time', data={
        'user_id': user.id,
    })
    assert res.status_code == 400

    res = client.post('/api/modify-time', data={
        'user_id': 'invalid',
        'operation': '+',
        'seconds': 'abc'
    })
    assert res.status_code == 400

    res = client.post('/api/modify-time', data={
        'user_id': user.id,
        'operation': '*',
        'seconds': '300'
    })
    assert res.status_code == 400
