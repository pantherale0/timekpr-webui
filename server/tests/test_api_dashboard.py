"""API and SSE hub tests for the realtime dashboard."""

import queue
import time
from datetime import datetime, timezone

import pytest

from src.database import AgentDevice, ManagedUser, ManagedUserDeviceMap, Settings
from src.dashboard_events import DashboardEventsHub, build_sse_snapshot


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


@pytest.fixture
def dashboard_user(db_session):
    device = AgentDevice(system_id='sys-dash', status='approved', secure_token='token')
    user = ManagedUser(username='dash-user', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-dash',
        linux_username='dash-user',
        is_valid=True,
        last_checked=datetime.now(timezone.utc),
    )
    db_session.add(mapping)
    db_session.commit()
    return user


def test_dashboard_snapshot_requires_auth(client):
    response = client.get('/api/dashboard')
    assert response.status_code == 401


def test_dashboard_events_requires_auth(client):
    response = client.get('/api/dashboard/events')
    assert response.status_code == 401


def test_dashboard_snapshot_authenticated(auth_client, dashboard_user):
    Settings.set_admin_password('admin')
    response = auth_client.get('/api/dashboard')
    assert response.status_code == 200

    payload = response.get_json()
    assert payload['success'] is True
    assert len(payload['users']) == 1

    user = payload['users'][0]
    assert user['id'] == dashboard_user.id
    assert user['username'] == 'dash-user'
    assert user['mapping_count'] == 1
    assert user['online_mapping_count'] == 0
    assert user['is_online'] is False
    assert 'time_left' in user
    assert 'usage_data' in user
    assert 'schedule_is_synced' in user
    assert 'last_checked_display' in user


def test_build_sse_snapshot_shape(dashboard_user):
    snapshot = build_sse_snapshot(reason='test')
    assert snapshot['type'] == 'snapshot'
    assert snapshot['reason'] == 'test'
    assert 'ts' in snapshot
    assert isinstance(snapshot['users'], list)
    assert isinstance(snapshot['pending_adjustments'], dict)


def test_dashboard_events_hub_debounces_notifications(app, db_session):
    hub = DashboardEventsHub(debounce_seconds=0.2)
    subscriber = hub.subscribe()

    with app.app_context():
        hub.notify_dashboard_changed('first')
        hub.notify_dashboard_changed('second')
        time.sleep(0.35)

        payloads = []
        while True:
            try:
                payloads.append(subscriber.get_nowait())
            except queue.Empty:
                break

    assert len(payloads) == 1
    assert payloads[0]['reason'] == 'second'
    hub.unsubscribe(subscriber)


def test_notify_dashboard_changed_delivers_to_subscriber(app, db_session):
    hub = DashboardEventsHub(debounce_seconds=0.05)
    subscriber = hub.subscribe()

    with app.app_context():
        hub.notify_dashboard_changed('unit_test')
        time.sleep(0.15)
        payload = subscriber.get(timeout=1)

    assert payload['type'] == 'snapshot'
    assert payload['reason'] == 'unit_test'
    hub.unsubscribe(subscriber)
