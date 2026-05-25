import json
import pytest
import hmac
import hashlib
from datetime import datetime
from unittest.mock import patch, MagicMock
from sqlalchemy import text
from app import ws_agent_handler, run_schema_migrations
from src.database import (
    AgentAlert,
    AgentDevice,
    BlocklistDomain,
    BlocklistSource,
    ManagedUser,
    ManagedUserBlocklistAssignment,
    ManagedUserDeviceMap,
    Settings,
    UserDailyTimeInterval,
    UserWeeklySchedule,
    db,
)
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
    user = ManagedUser(username="jack", system_ip="Unassigned", is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id="sys-1",
        linux_username="jack",
        is_valid=True
    )
    db_session.add(mapping)
    db_session.commit()

    res = client.get('/dashboard')
    assert res.status_code == 200
    assert b"jack" in res.data

def test_admin_panel(client, db_session):
    Settings.set_admin_password("admin")
    client.post('/', data={'username': 'admin', 'password': 'admin'})

    pending_device = AgentDevice(system_id="family-alpha-aa", system_hostname="family-pc", status="pending")
    approved_device = AgentDevice(
        system_id="family-beta-bb",
        system_hostname="family-pc",
        status="approved",
        secure_token="token",
    )
    user = ManagedUser(username="alice", system_ip="Unassigned", is_valid=False)
    db_session.add_all([pending_device, approved_device, user])
    db_session.flush()
    db_session.add(ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=approved_device.system_id,
        linux_username="alice",
        is_valid=False,
    ))
    db_session.commit()

    res = client.get('/admin')
    assert res.status_code == 200
    assert b"Admin Panel" in res.data
    assert b"family-pc (aa)" in res.data
    assert b"family-pc (bb)" in res.data

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

    # POST alert webhook settings
    res = client.post('/settings', data={
        'form_name': 'alert_webhook',
        'alert_webhook_enabled': 'on',
        'alert_webhook_url': 'https://hooks.example.test/timekpr',
        'alert_webhook_secret': 'secret-value',
    }, follow_redirects=True)
    assert b"Alert webhook settings updated successfully" in res.data
    assert Settings.get_value('alert_webhook_enabled') == '1'
    assert Settings.get_value('alert_webhook_url') == 'https://hooks.example.test/timekpr'
    assert Settings.get_value('alert_webhook_secret') == 'secret-value'

    # Enabled without URL should fail validation
    res = client.post('/settings', data={
        'form_name': 'alert_webhook',
        'alert_webhook_enabled': 'on',
        'alert_webhook_url': '',
        'alert_webhook_secret': '',
    }, follow_redirects=True)
    assert b'Webhook URL is required when alert delivery is enabled' in res.data


def test_blocklist_catalog_and_assignment_routes(client, db_session):
    Settings.set_admin_password("admin")
    client.post('/', data={'username': 'admin', 'password': 'admin'})

    device = AgentDevice(system_id="policy-device", system_hostname="study-pc", status="approved", secure_token="tok")
    user = ManagedUser(username="alice", system_ip="Unassigned", is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=device.system_id,
        linux_username="alice",
        linux_uid=1001,
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()

    res = client.post('/blocklists/sources/add', data={
        'name': 'School Hours',
        'source_type': 'manual',
        'manual_domains': 'dns.google\ncloudflare-dns.com\n',
    }, follow_redirects=True)
    assert b'created with 2 domain(s)' in res.data

    source = BlocklistSource.query.filter_by(name='School Hours').first()
    assert source is not None
    assert source.domain_count == 2

    res = client.post(
        f'/blocklists/sources/{source.id}/domains/add',
        data={'domain': 'example.com'},
        follow_redirects=True,
    )
    assert b'Added example.com' in res.data
    assert BlocklistDomain.query.filter_by(source_id=source.id, domain='example.com').first() is not None

    res = client.post(
        f'/managed-users/{user.id}/blocklists/update',
        data={'source_ids': [str(source.id)]},
        follow_redirects=True,
    )
    assert b'Updated blocklist assignments for alice' in res.data
    assert ManagedUserBlocklistAssignment.query.filter_by(managed_user_id=user.id, source_id=source.id).first() is not None

    sync_status = client.get(f'/api/user/{user.id}/blocklists/sync-status')
    assert sync_status.status_code == 200
    status_payload = json.loads(sync_status.data)
    assert status_payload['success']
    assert status_payload['assigned_source_count'] == 1
    assert status_payload['effective_domain_count'] == 3
    assert status_payload['mapping_count'] == 1

    weekly_page = client.get(f'/weekly-schedule/{user.id}')
    assert weekly_page.status_code == 200
    assert b'Internet Blocklists' in weekly_page.data
    assert b'School Hours' in weekly_page.data

    device_page = client.get(f'/devices/{device.system_id}')
    assert device_page.status_code == 200
    assert b'Domain Policy Contributors' in device_page.data
    assert b'Lists: 1' in device_page.data

def test_user_operations(client, db_session):
    Settings.set_admin_password("admin")
    client.post('/', data={'username': 'admin', 'password': 'admin'})

    # Approve a device so we can register a user to it
    device = AgentDevice(system_id="device-abc", system_hostname="laptop", status="approved", secure_token="tkn")
    db_session.add(device)
    db_session.commit()

    # 1. Add new user - missing username/system_id
    res = client.post('/users/add', data={'username': '', 'system_id': ''}, follow_redirects=True)
    assert b"Both username and device are required" in res.data

    # 2. Add new user - device not approved
    res = client.post('/users/add', data={'username': 'bob', 'system_id': 'device-unapproved'}, follow_redirects=True)
    assert b"is not registered or approved" in res.data

    # 3. Add new user - success
    res = client.post('/users/add', data={'username': 'bob', 'system_id': 'device-abc'}, follow_redirects=True)
    assert b"and mapping added" in res.data

    # 4. Add existing user
    res = client.post('/users/add', data={'username': 'bob', 'system_id': 'device-abc'}, follow_redirects=True)
    assert b"already exists" in res.data

    # Retrieve bob's user record
    bob_user = ManagedUser.query.filter_by(username="bob").first()
    assert bob_user is not None

    # 4b. Create managed user using new endpoint
    res = client.post('/managed-users/add', data={'username': 'alice'}, follow_redirects=True)
    assert b"Managed user alice created" in res.data
    alice = ManagedUser.query.filter_by(username="alice").first()
    assert alice is not None

    # 4c. Add mapping using new endpoint
    res = client.post(
        f'/managed-users/{alice.id}/mappings/add',
        data={'system_id': 'device-abc', 'linux_username': 'alice', 'linux_uid': '1001'},
        follow_redirects=True
    )
    assert b"Mapping added: alice -&gt; alice@laptop" in res.data
    alice_mapping = ManagedUserDeviceMap.query.filter_by(managed_user_id=alice.id, system_id='device-abc').first()
    assert alice_mapping is not None

    # 5. Validate user manual triggers
    with patch('src.agent_helper.AgentClient.validate_user') as mock_val:
        mock_val.return_value = (True, "Valid User", {"TIME_SPENT_DAY": 1200})
        res = client.get(f'/users/validate/{bob_user.id}', follow_redirects=True)
        assert b"Validated" in res.data

    # 6. Delete user
    res = client.post(f'/users/delete/{bob_user.id}', follow_redirects=True)
    assert b"removed successfully" in res.data

def test_rest_apis(client, db_session):
    Settings.set_admin_password("admin")
    client.post('/', data={'username': 'admin', 'password': 'admin'})

    # Setup devices
    device_pending = AgentDevice(system_id="sys-pending-aa", system_hostname="family-pc", status="pending")
    device_approved = AgentDevice(
        system_id="sys-approved-bb",
        system_hostname="family-pc",
        status="approved",
        secure_token="some-tkn",
    )
    db_session.add_all([device_pending, device_approved])
    db_session.commit()

    # Approve Device API - Device Not Found
    res = client.post('/api/device/approve/sys-none')
    assert res.status_code == 404

    # Approve Device API - Device not pending
    res = client.post('/api/device/approve/sys-approved-bb')
    assert res.status_code == 400

    # Approve Device API - Success
    res = client.post('/api/device/approve/sys-pending-aa')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['success']
    assert data['message'] == 'Device family-pc (aa) approved successfully.'
    assert device_pending.status == "approved"
    assert device_pending.secure_token is not None

    # Reject Device API - Device Not Found
    res = client.post('/api/device/reject/sys-none')
    assert res.status_code == 404

    # Reject Device API - Success
    res = client.post('/api/device/reject/sys-approved-bb')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['success']
    assert data['message'] == 'Device family-pc (bb) rejected successfully.'
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
        "system_id": "sys-new-pending",
        "system_hostname": "kids-pc",
    })])
    with app.test_request_context('/ws', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        ws_agent_handler(ws_pending_new)
    # Check that it gets marked pending and pairing_status sent
    assert json.loads(ws_pending_new.sent_messages[0])['type'] == "pairing_status"
    assert json.loads(ws_pending_new.sent_messages[0])['status'] == "pending"
    pending_device = AgentDevice.query.get("sys-new-pending")
    assert pending_device.system_hostname == "kids-pc"

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
                self.messages.append(json.dumps({
                    "type": "alert_event",
                    "event_type": "system_startup",
                    "occurred_at": "2026-05-25T21:05:00Z",
                    "details": {"source": "test-suite"}
                }))

    ws_flow = FlowWS([hello_msg])
    with app.test_request_context('/ws', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        ws_agent_handler(ws_flow)

    # Check that challenge was sent and auth_result was successful
    assert len(ws_flow.sent_messages) == 2
    assert json.loads(ws_flow.sent_messages[0])['type'] == "challenge"
    assert json.loads(ws_flow.sent_messages[1])['type'] == "auth_result"
    assert json.loads(ws_flow.sent_messages[1])['success'] is True

    stored_alert = AgentAlert.query.filter_by(system_id=system_id, event_type='system_startup').first()
    assert stored_alert is not None
    assert stored_alert.delivery_status == AgentAlert.DELIVERY_DISABLED
    assert stored_alert.payload["details"]["source"] == "test-suite"

    class InvalidAlertWS(MockWS):
        def send(self, data):
            super().send(data)
            payload = json.loads(data)
            if payload.get("type") == "challenge":
                challenge = payload.get("challenge")
                token_bytes = token.encode('utf-8')
                msg = (challenge + invalid_system_id).encode('utf-8')
                signature = hmac.new(token_bytes, msg, hashlib.sha256).hexdigest()
                self.messages.append(json.dumps({
                    "type": "register",
                    "signature": signature
                }))
                self.messages.append(json.dumps({
                    "type": "alert_event",
                    "event_type": "invalid_type",
                    "occurred_at": "2026-05-25T21:05:00Z",
                    "details": {"source": "bad-payload"}
                }))

    invalid_system_id = "approved-system-invalid"
    invalid_device = AgentDevice(system_id=invalid_system_id, status="approved", secure_token=token)
    db_session.add(invalid_device)
    db_session.commit()

    invalid_ws = InvalidAlertWS([json.dumps({"type": "hello", "system_id": invalid_system_id})])
    with app.test_request_context('/ws', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        ws_agent_handler(invalid_ws)

    assert AgentAlert.query.filter_by(system_id=invalid_system_id).count() == 0

def test_new_endpoints(client, db_session):
    Settings.set_admin_password("admin")
    client.post('/', data={'username': 'admin', 'password': 'admin'})

    device = AgentDevice(system_id="sys-new", status="approved", secure_token="tkn")
    user = ManagedUser(username="jack", system_ip="Unassigned", is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id="sys-new",
        linux_username="jack",
        is_valid=True
    )
    db_session.add(mapping)
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

    user_no_sched = ManagedUser(username="nosched", system_ip="Unassigned")
    db_session.add(user_no_sched)
    db_session.flush()
    db_session.add(ManagedUserDeviceMap(
        managed_user_id=user_no_sched.id,
        system_id="sys-new",
        linux_username="nosched",
    ))
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
    assert data['step_minutes'] == 15
    assert data['intervals']['1'] == []

    interval_data = {
        'intervals': {
            '1': [
                {
                    'start_hour': 9,
                    'start_minute': 0,
                    'end_hour': 11,
                    'end_minute': 0,
                    'is_enabled': True
                },
                {
                    'start_hour': 15,
                    'start_minute': 0,
                    'end_hour': 17,
                    'end_minute': 30,
                    'is_enabled': True
                }
            ],
            '2': []
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

    day_one_intervals = UserDailyTimeInterval.query.filter_by(
        user_id=user.id,
        day_of_week=1
    ).order_by(UserDailyTimeInterval.sort_order).all()
    assert len(day_one_intervals) == 2
    assert day_one_intervals[0].start_hour == 9
    assert day_one_intervals[1].end_minute == 30
    assert not day_one_intervals[0].is_synced
    hidden_day_two = UserDailyTimeInterval.query.filter_by(user_id=user.id, day_of_week=2).all()
    assert len(hidden_day_two) == 1
    assert hidden_day_two[0].is_enabled is False

    res = client.get(f'/api/user/{user.id}/intervals')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert len(data['intervals']['1']) == 2
    assert data['intervals']['1'][0]['sort_order'] == 0
    assert data['intervals']['1'][1]['time_range'] == "15:00-17:30"

    invalid_interval_data = {
        'intervals': {
            '1': [
                {
                    'start_hour': 10,
                    'start_minute': 0,
                    'end_hour': 12,
                    'end_minute': 0,
                    'is_enabled': True
                },
                {
                    'start_hour': 11,
                    'start_minute': 45,
                    'end_hour': 13,
                    'end_minute': 0,
                    'is_enabled': True
                }
            ]
        }
    }
    res = client.post(
        f'/api/user/{user.id}/intervals/update',
        data=json.dumps(invalid_interval_data),
        content_type='application/json'
    )
    assert res.status_code == 400

    invalid_step_data = {
        'intervals': {
            '1': [
                {
                    'start_hour': 18,
                    'start_minute': 10,
                    'end_hour': 19,
                    'end_minute': 0,
                    'is_enabled': True
                }
            ]
        }
    }
    res = client.post(
        f'/api/user/{user.id}/intervals/update',
        data=json.dumps(invalid_step_data),
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


def test_alert_pages_for_user_and_device(client, db_session):
    Settings.set_admin_password("admin")
    client.post('/', data={'username': 'admin', 'password': 'admin'})

    device = AgentDevice(
        system_id="device-alert-aa",
        system_hostname="family-pc",
        system_ip="10.0.0.22",
        status="approved",
        secure_token="tok",
    )
    user = ManagedUser(username="jack", system_ip="Unassigned", is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    db_session.add(ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=device.system_id,
        linux_username="jack",
        is_valid=True,
    ))
    db_session.add_all([
        AgentAlert(
            system_id=device.system_id,
            event_type='user_signed_in',
            linux_username='jack',
            occurred_at=datetime.utcnow(),
            payload_json=json.dumps({
                'system_id': device.system_id,
                'event_type': 'user_signed_in',
                'linux_username': 'jack',
                'details': {'session_id': 'c1', 'source': 'login'},
            }),
            webhook_enabled_snapshot=False,
            delivery_status=AgentAlert.DELIVERY_DISABLED,
        ),
        AgentAlert(
            system_id=device.system_id,
            event_type='system_sleep',
            linux_username=None,
            occurred_at=datetime.utcnow(),
            payload_json=json.dumps({
                'system_id': device.system_id,
                'event_type': 'system_sleep',
                'details': {'phase': 'prepare'},
            }),
            webhook_enabled_snapshot=False,
            delivery_status=AgentAlert.DELIVERY_DISABLED,
        ),
        AgentAlert(
            system_id=device.system_id,
            event_type='user_signed_out',
            linux_username='other-user',
            occurred_at=datetime.utcnow(),
            payload_json=json.dumps({
                'system_id': device.system_id,
                'event_type': 'user_signed_out',
                'linux_username': 'other-user',
                'details': {'session_id': 'hidden'},
            }),
            webhook_enabled_snapshot=False,
            delivery_status=AgentAlert.DELIVERY_DISABLED,
        ),
    ])
    db_session.commit()

    res = client.get(f'/stats/{user.id}')
    assert res.status_code == 200
    assert b'Alert Audit' in res.data
    assert b'User Signed In' in res.data
    assert b'System Sleep' in res.data
    assert b'other-user' not in res.data
    assert f'/devices/{device.system_id}'.encode() in res.data

    filtered = client.get(f'/stats/{user.id}?alert_search=prepare')
    assert filtered.status_code == 200
    assert b'System Sleep' in filtered.data
    assert b'User Signed In' not in filtered.data

    device_page = client.get(f'/devices/{device.system_id}')
    assert device_page.status_code == 200
    assert b'Device details, linked accounts, and alert history' in device_page.data
    assert b'jack -> jack' in device_page.data
    assert b'other-user' in device_page.data

    device_filtered = client.get(f'/devices/{device.system_id}?alert_search=other-user')
    assert device_filtered.status_code == 200
    assert b'other-user' in device_filtered.data
    assert b'User Signed In' not in device_filtered.data

    admin_res = client.get('/admin')
    assert admin_res.status_code == 200
    assert f'/devices/{device.system_id}'.encode() in admin_res.data

def test_run_schema_migrations_upgrades_time_intervals(app, db_session):
    user = ManagedUser(username="legacy-user", system_ip="Unassigned")
    db_session.add(user)
    db_session.commit()

    db_session.execute(text("DROP TABLE user_daily_time_interval"))
    db_session.execute(text("""
        CREATE TABLE user_daily_time_interval (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL,
            start_hour INTEGER NOT NULL,
            start_minute INTEGER DEFAULT 0,
            end_hour INTEGER NOT NULL,
            end_minute INTEGER DEFAULT 0,
            is_enabled BOOLEAN DEFAULT 1,
            is_synced BOOLEAN DEFAULT 0,
            last_synced DATETIME NULL,
            last_modified DATETIME NULL,
            FOREIGN KEY(user_id) REFERENCES managed_user(id),
            UNIQUE(user_id, day_of_week)
        )
    """))
    db_session.execute(text("""
        INSERT INTO user_daily_time_interval (
            user_id,
            day_of_week,
            start_hour,
            start_minute,
            end_hour,
            end_minute,
            is_enabled,
            is_synced
        ) VALUES (
            :user_id,
            1,
            9,
            0,
            17,
            0,
            1,
            0
        )
    """), {"user_id": user.id})
    db_session.commit()

    with app.app_context():
        run_schema_migrations()

    columns = {
        row[1]
        for row in db_session.execute(text("PRAGMA table_info(user_daily_time_interval)")).fetchall()
    }
    assert 'sort_order' in columns

    migrated_intervals = UserDailyTimeInterval.query.filter_by(user_id=user.id).all()
    assert len(migrated_intervals) == 1
    assert migrated_intervals[0].sort_order == 0


def test_run_schema_migrations_adds_device_hostname_column(app, db_session):
    db_session.execute(text("DROP TABLE IF EXISTS managed_user_device_map"))
    db_session.execute(text("DROP TABLE agent_device"))
    db_session.execute(text("""
        CREATE TABLE agent_device (
            system_id VARCHAR(50) PRIMARY KEY,
            system_ip VARCHAR(50) NULL,
            status VARCHAR(20) DEFAULT 'pending',
            secure_token VARCHAR(64) NULL,
            date_added DATETIME NULL,
            last_seen DATETIME NULL
        )
    """))
    db_session.execute(text("""
        INSERT INTO agent_device (
            system_id,
            system_ip,
            status,
            secure_token
        ) VALUES (
            'legacy-device-aa',
            '127.0.0.1',
            'pending',
            NULL
        )
    """))
    db_session.commit()

    with app.app_context():
        run_schema_migrations()

    columns = {
        row[1]
        for row in db_session.execute(text("PRAGMA table_info(agent_device)")).fetchall()
    }
    assert 'system_hostname' in columns

    legacy_device = AgentDevice.query.get('legacy-device-aa')
    assert legacy_device is not None
    assert legacy_device.system_hostname is None


def test_run_schema_migrations_creates_agent_alert_table(app, db_session):
    db_session.execute(text("DROP TABLE IF EXISTS agent_alert"))
    db_session.commit()

    with app.app_context():
        run_schema_migrations()

    columns = {
        row[1]
        for row in db_session.execute(text("PRAGMA table_info(agent_alert)")).fetchall()
    }
    assert 'event_type' in columns
    assert 'payload_json' in columns
    assert 'delivery_status' in columns


def test_run_schema_migrations_creates_blocklist_tables_and_mapping_sync_columns(app, db_session):
    with app.app_context():
        run_schema_migrations()

    mapping_columns = {
        row[1]
        for row in db_session.execute(text("PRAGMA table_info(managed_user_device_map)")).fetchall()
    }
    assert 'blocklist_policy_hash' in mapping_columns
    assert 'blocklist_is_synced' in mapping_columns
    assert 'blocklist_last_synced' in mapping_columns
    assert 'blocklist_last_error' in mapping_columns

    source_columns = {
        row[1]
        for row in db_session.execute(text("PRAGMA table_info(blocklist_source)")).fetchall()
    }
    assert 'name' in source_columns
    assert 'source_type' in source_columns
    assert 'etag' in source_columns

    domain_columns = {
        row[1]
        for row in db_session.execute(text("PRAGMA table_info(blocklist_domain)")).fetchall()
    }
    assert 'source_id' in domain_columns
    assert 'domain' in domain_columns

    assignment_columns = {
        row[1]
        for row in db_session.execute(text("PRAGMA table_info(managed_user_blocklist_assignment)")).fetchall()
    }
    assert 'managed_user_id' in assignment_columns
    assert 'source_id' in assignment_columns
