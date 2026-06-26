"""API tests for user statistics, per-app usage, and advanced alerts filtering."""

import pytest
from datetime import datetime, timezone, timedelta, date
from src.models import (
    AgentDevice,
    ManagedUser,
    ManagedUserDeviceMap,
    UserTimeUsage,
    AppUsageHistory,
    AgentAlert,
)

@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client

@pytest.fixture
def stats_test_setup(db_session):
    # Setup Device
    device = AgentDevice(system_id='sys-stats-test', status='approved', secure_token='token')
    # Setup User
    user = ManagedUser(username='stats-user', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()

    # Setup Mapping
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=device.system_id,
        linux_username='stats-user-unix',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.flush()

    # Setup overall usage (UserTimeUsage)
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    
    usage1 = UserTimeUsage(user_id=user.id, date=today, time_spent=3600) # 1 hour
    usage2 = UserTimeUsage(user_id=user.id, date=yesterday, time_spent=7200) # 2 hours
    db_session.add_all([usage1, usage2])

    # Setup per-app usage (AppUsageHistory)
    start_dt = datetime.now(timezone.utc) - timedelta(hours=2)
    end_dt = datetime.now(timezone.utc) - timedelta(hours=1)
    app_usage = AppUsageHistory(
        device_map_id=mapping.id,
        application_name='Firefox Web Browser',
        executable_path='/usr/bin/firefox',
        start_time=start_dt,
        end_time=end_dt,
        duration_seconds=3600,
    )
    db_session.add(app_usage)

    # Setup alerts including terminal commands
    alert = AgentAlert(
        system_id=device.system_id,
        event_type='blocklist_blocked',
        linux_username='stats-user-unix',
        occurred_at=datetime.now(timezone.utc),
        payload_json='{"details": {"domain": "badsite.com"}}',
    )
    command = AgentAlert(
        system_id=device.system_id,
        event_type='terminal_command',
        linux_username='stats-user-unix',
        occurred_at=datetime.now(timezone.utc),
        payload_json='{"details": {"cmd": "whoami", "pwd": "/home/user"}}',
    )
    db_session.add_all([alert, command])

    db_session.commit()
    return user, device

def test_get_user_stats_requires_auth(client):
    response = client.get('/api/user/1/stats')
    assert response.status_code == 401

def test_get_user_stats_not_found(auth_client):
    response = auth_client.get('/api/user/99999/stats')
    assert response.status_code == 404

def test_get_user_stats_success(auth_client, stats_test_setup):
    user, device = stats_test_setup
    today_str = datetime.now(timezone.utc).date().strftime('%Y-%m-%d')
    yesterday_str = (datetime.now(timezone.utc).date() - timedelta(days=1)).strftime('%Y-%m-%d')

    response = auth_client.get(f'/api/user/{user.id}/stats?start_date={yesterday_str}&end_date={today_str}')
    assert response.status_code == 200

    payload = response.get_json()
    assert payload['success'] is True
    assert payload['username'] == 'stats-user'
    
    # Check overall summary
    summary = payload['summary']
    assert summary['total_seconds'] == 10800 # 3 hours total
    assert summary['daily_average_seconds'] == 5400 # 1.5 hours avg
    assert summary['peak_seconds'] == 7200
    assert summary['peak_date'] == yesterday_str

    # Check daily trend
    daily = payload['daily_usage']
    assert daily[today_str] == 3600
    assert daily[yesterday_str] == 7200

    # Check per-application usage
    app_usage = payload['app_usage']
    assert len(app_usage) == 1
    assert app_usage[0]['application_name'] == 'Firefox Web Browser'
    assert app_usage[0]['executable_path'] == '/usr/bin/firefox'
    assert app_usage[0]['total_seconds'] == 3600

def test_alerts_api_date_filtering(auth_client, stats_test_setup):
    user, device = stats_test_setup
    today_str = datetime.now(timezone.utc).date().strftime('%Y-%m-%d')
    tomorrow_str = (datetime.now(timezone.utc).date() + timedelta(days=1)).strftime('%Y-%m-%d')
    yesterday_str = (datetime.now(timezone.utc).date() - timedelta(days=1)).strftime('%Y-%m-%d')

    # Query matching range
    response = auth_client.get(f'/api/alerts?managed_user_id={user.id}&start_date={yesterday_str}&end_date={tomorrow_str}')
    assert response.status_code == 200
    payload = response.get_json()
    # By default, doesn't include terminal commands
    assert len(payload['data']['alerts']) == 1
    assert payload['data']['alerts'][0]['event_type'] == 'blocklist_blocked'

    # Query out-of-range (e.g. tomorrow to next day)
    next_day_str = (datetime.now(timezone.utc).date() + timedelta(days=2)).strftime('%Y-%m-%d')
    response2 = auth_client.get(f'/api/alerts?managed_user_id={user.id}&start_date={tomorrow_str}&end_date={next_day_str}')
    assert response2.status_code == 200
    payload2 = response2.get_json()
    assert len(payload2['data']['alerts']) == 0

def test_alerts_api_include_commands(auth_client, stats_test_setup):
    user, device = stats_test_setup
    today_str = datetime.now(timezone.utc).date().strftime('%Y-%m-%d')

    # Query with include_commands=true
    response = auth_client.get(f'/api/alerts?managed_user_id={user.id}&include_commands=true')
    assert response.status_code == 200
    payload = response.get_json()
    
    # Should contain both blocklist alert and terminal command alert
    alerts = payload['data']['alerts']
    assert len(alerts) == 2
    event_types = {a['event_type'] for a in alerts}
    assert 'blocklist_blocked' in event_types
    assert 'terminal_command' in event_types
