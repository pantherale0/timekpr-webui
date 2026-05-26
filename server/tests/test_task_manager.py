import json
from datetime import datetime, date, timedelta
from src.task_manager import BackgroundTaskManager
from src.database import (
    AgentAlert,
    BlocklistDomain,
    BlocklistSource,
    ManagedUser,
    ManagedUserBlocklistAssignment,
    ManagedUserDeviceMap,
    UserTimeUsage,
    UserWeeklySchedule,
    UserDailyTimeInterval,
    AgentDevice,
    Settings,
)
from src.agent_helper import AgentConnectionManager

class DummyWS:
    def __init__(self):
        self.sent_messages = []
        
    def send(self, message):
        self.sent_messages.append(message)
        try:
            payload = json.loads(message)
            correlation_id = payload.get("correlation_id")
            if correlation_id:
                action = payload.get("action")
                data = {"config": {"TIME_SPENT_DAY": 450, "TIME_LEFT_DAY": 1000}}
                if action == "get_domain_policy_state":
                    data = {"source_revisions": {}}
                # Default response
                AgentConnectionManager.route_response(correlation_id, {
                    "success": True,
                    "message": "Success",
                    "data": data,
                })
        except Exception:
            pass


class StatefulTimeWS:
    def __init__(self, time_spent=450, time_left=1000):
        self.sent_messages = []
        self.time_spent = time_spent
        self.time_left = time_left

    def send(self, message):
        self.sent_messages.append(message)
        payload = json.loads(message)
        correlation_id = payload.get("correlation_id")
        if not correlation_id:
            return

        action = payload.get("action")
        if action == "modify_time_left":
            operation = payload.get("args", {}).get("operation")
            seconds = int(payload.get("args", {}).get("seconds", 0))
            if operation == "+":
                self.time_left += seconds
            elif operation == "-":
                self.time_left -= seconds

        data = {"config": {"TIME_SPENT_DAY": self.time_spent, "TIME_LEFT_DAY": self.time_left}}
        if action == "get_domain_policy_state":
            data = {"source_revisions": {}}

        AgentConnectionManager.route_response(correlation_id, {
            "success": True,
            "message": "Success",
            "data": data,
        })

from unittest.mock import MagicMock

def test_task_manager_basic_ops(app, db_session):
    manager = BackgroundTaskManager(app)
    manager._run_tasks = MagicMock()
    assert manager.app == app

    # Test init_app
    manager2 = BackgroundTaskManager()
    manager2.init_app(app)
    assert manager2.app == app

    # Test start and stop
    manager.start()
    assert manager.running
    status = manager.get_status()
    assert status['running']

    # Test stop
    manager.stop()
    assert not manager.running
    
    # Test double start does nothing
    manager.running = True
    manager.start()
    manager.running = False
    
    # Restart
    manager.restart()
    assert manager.running
    manager.stop()


def test_task_manager_runs_only_enabled_task_roles(app, db_session):
    manager = BackgroundTaskManager(
        app,
        refresh_external_blocklists=False,
        update_user_data=True,
        sync_domain_policies=False,
        deliver_pending_alerts=True,
    )
    manager._update_user_data = MagicMock()
    manager._sync_domain_policies = MagicMock()
    manager._refresh_external_blocklists = MagicMock()
    manager._deliver_pending_alerts = MagicMock()

    with app.app_context():
        manager._run_task_cycle()

    manager._update_user_data.assert_called_once()
    manager._deliver_pending_alerts.assert_called_once()
    manager._sync_domain_policies.assert_not_called()
    manager._refresh_external_blocklists.assert_not_called()


def test_task_manager_does_not_poll_domain_policies_in_task_cycle(app, db_session):
    manager = BackgroundTaskManager(
        app,
        refresh_external_blocklists=False,
        update_user_data=False,
        sync_domain_policies=True,
        deliver_pending_alerts=False,
    )
    manager._sync_domain_policies = MagicMock()

    with app.app_context():
        manager._run_task_cycle()

    manager._sync_domain_policies.assert_not_called()

def test_task_manager_update_user_data(app, db_session):
    manager = BackgroundTaskManager(app)

    # 1. Create a user who is offline
    user_offline = ManagedUser(
        username="offline_user",
        system_ip="Unassigned",
        is_valid=True
    )
    device_offline = AgentDevice(system_id="sys-offline", status="approved", secure_token="tok")
    db_session.add(user_offline)
    db_session.flush()
    offline_mapping = ManagedUserDeviceMap(
        managed_user_id=user_offline.id,
        system_id="sys-offline",
        linux_username="offline_user",
        is_valid=True,
    )
    db_session.add_all([device_offline, offline_mapping])
    db_session.commit()

    # Verify task manager skips offline user
    with app.app_context():
        manager._update_user_data()
        
    # Check that offline user was checked but unchanged
    updated_offline = ManagedUser.query.filter_by(username="offline_user").first()
    assert updated_offline.last_checked is not None

    # 2. Create online user with pending time adjustment
    user_online = ManagedUser(
        username="online_user",
        system_ip="Unassigned",
        is_valid=True,
        pending_time_adjustment=300,
        pending_time_operation="+"
    )
    device_online = AgentDevice(system_id="sys-online", status="approved", secure_token="tok")
    db_session.add_all([user_online, device_online])
    db_session.flush()
    online_mapping = ManagedUserDeviceMap(
        managed_user_id=user_online.id,
        system_id="sys-online",
        linux_username="online_user",
        is_valid=True,
        last_config='{"TIME_SPENT_DAY": 450, "TIME_LEFT_DAY": 1000}'
    )
    db_session.add(online_mapping)
    db_session.commit()

    ws = DummyWS()
    AgentConnectionManager.register("sys-online", ws, "10.0.0.2")

    # Run tasks
    with app.app_context():
        manager._update_user_data()

    # Verify pending adjustment cleared
    updated_online = ManagedUser.query.filter_by(username="online_user").first()
    assert updated_online.pending_time_adjustment is None
    assert updated_online.pending_time_operation is None

    # 3. Test weekly schedule synchronization
    # Case A: Schedule has all zero limits -> automatically marked synced
    schedule_zeros = UserWeeklySchedule(user_id=user_online.id, is_synced=False)
    db_session.add(schedule_zeros)
    db_session.commit()

    with app.app_context():
        manager._update_user_data()
    
    assert schedule_zeros.is_synced

    # Case B: Schedule has positive limits -> calls agent limits
    schedule_zeros.monday_hours = 2.0
    schedule_zeros.is_synced = False
    db_session.commit()

    with app.app_context():
        manager._update_user_data()

    assert schedule_zeros.is_synced

    # 4. Test daily time intervals synchronization
    interval = UserDailyTimeInterval(
        user_id=user_online.id,
        day_of_week=1,
        sort_order=0,
        start_hour=9,
        start_minute=0,
        end_hour=17,
        end_minute=0,
        is_synced=False
    )
    second_interval = UserDailyTimeInterval(
        user_id=user_online.id,
        day_of_week=1,
        sort_order=1,
        start_hour=18,
        start_minute=30,
        end_hour=20,
        end_minute=0,
        is_synced=False
    )
    db_session.add_all([interval, second_interval])
    db_session.commit()

    with app.app_context():
        manager._update_user_data()

    assert interval.is_synced
    assert second_interval.is_synced

    allowed_hours_calls = [
        json.loads(message)
        for message in ws.sent_messages
        if json.loads(message).get("action") == "set_allowed_hours"
    ]
    assert allowed_hours_calls
    day_one = allowed_hours_calls[-1]["args"]["intervals"]["1"]
    assert day_one["9"] == {"STARTMIN": 0, "ENDMIN": 60, "UACC": 0}
    assert day_one["16"] == {"STARTMIN": 0, "ENDMIN": 60, "UACC": 0}
    assert day_one["18"] == {"STARTMIN": 30, "ENDMIN": 60, "UACC": 0}
    assert day_one["19"] == {"STARTMIN": 0, "ENDMIN": 60, "UACC": 0}

    # 5. User validation failure handling
    # If validate_user fails or connection throws error
    # We unregister online user to trigger exception
    AgentConnectionManager.unregister("sys-online")
    
    with app.app_context():
        manager._update_user_data()
        
    # When all mappings are offline, user becomes invalid until validation recovers
    assert not user_online.is_valid

    # Clean up registry
    AgentConnectionManager.unregister("sys-offline")
    AgentConnectionManager.unregister("sys-online")


def test_task_manager_retains_same_day_usage_for_offline_mappings(app, db_session):
    manager = BackgroundTaskManager(app)

    user = ManagedUser(
        username="shared_user",
        system_ip="Unassigned",
        is_valid=True,
    )
    offline_device = AgentDevice(system_id="sys-cached", status="approved", secure_token="tok")
    online_device = AgentDevice(system_id="sys-live", status="approved", secure_token="tok")
    db_session.add_all([user, offline_device, online_device])
    db_session.flush()

    offline_mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id="sys-cached",
        linux_username="shared_user",
        is_valid=True,
        last_checked=datetime.utcnow(),
        last_config='{"TIME_SPENT_DAY": 1800, "TIME_LEFT_DAY": 1200}',
    )
    online_mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id="sys-live",
        linux_username="shared_user",
        is_valid=True,
    )
    db_session.add_all([offline_mapping, online_mapping])
    db_session.commit()

    ws = DummyWS()
    AgentConnectionManager.register("sys-live", ws, "10.0.0.4")

    with app.app_context():
        manager._update_user_data()

    refreshed_user = ManagedUser.query.filter_by(id=user.id).first()
    usage = UserTimeUsage.query.filter_by(user_id=user.id, date=date.today()).first()
    assert usage.time_spent == 2250
    assert refreshed_user.get_config_value("TIME_SPENT_DAY") == 2250

    AgentConnectionManager.unregister("sys-live")

    with app.app_context():
        manager._update_user_data()

    refreshed_user = ManagedUser.query.filter_by(id=user.id).first()
    usage = UserTimeUsage.query.filter_by(user_id=user.id, date=date.today()).first()
    assert usage.time_spent == 2250
    assert refreshed_user.get_config_value("TIME_SPENT_DAY") == 2250


def test_task_manager_rebalances_shared_time_left_across_devices(app, db_session):
    manager = BackgroundTaskManager(app)

    user = ManagedUser(
        username="shared_limit_user",
        system_ip="Unassigned",
        is_valid=True,
    )
    offline_device = AgentDevice(system_id="sys-offline-balance", status="approved", secure_token="tok")
    online_device = AgentDevice(system_id="sys-online-balance", status="approved", secure_token="tok")
    db_session.add_all([user, offline_device, online_device])
    db_session.flush()

    schedule = UserWeeklySchedule(user_id=user.id, is_synced=True)
    weekday_columns = (
        'monday_hours',
        'tuesday_hours',
        'wednesday_hours',
        'thursday_hours',
        'friday_hours',
        'saturday_hours',
        'sunday_hours',
    )
    setattr(schedule, weekday_columns[date.today().weekday()], 1.0)
    db_session.add(schedule)

    offline_mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id="sys-offline-balance",
        linux_username="shared_limit_user",
        is_valid=True,
        last_checked=datetime.utcnow(),
        last_config='{"TIME_SPENT_DAY": 1200, "TIME_LEFT_DAY": 2400}',
    )
    online_mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id="sys-online-balance",
        linux_username="shared_limit_user",
        is_valid=True,
    )
    db_session.add_all([offline_mapping, online_mapping])
    db_session.commit()

    ws = StatefulTimeWS(time_spent=600, time_left=3000)
    AgentConnectionManager.register("sys-online-balance", ws, "10.0.0.5")

    with app.app_context():
        manager._update_user_data()

    rebalance_calls = [
        json.loads(message)
        for message in ws.sent_messages
        if json.loads(message).get("action") == "modify_time_left"
    ]
    assert rebalance_calls
    assert rebalance_calls[-1]["args"] == {"operation": "-", "seconds": 1200}

    refreshed_user = ManagedUser.query.filter_by(id=user.id).first()
    usage = UserTimeUsage.query.filter_by(user_id=user.id, date=date.today()).first()
    assert usage.time_spent == 1800
    assert refreshed_user.get_config_value("TIME_LEFT_DAY") == 1800
    assert online_mapping.get_config_value("TIME_LEFT_DAY") == 1800

    AgentConnectionManager.unregister("sys-online-balance")


def test_task_manager_uses_server_daily_adjustment_when_rebalancing(app, db_session):
    manager = BackgroundTaskManager(app)

    user = ManagedUser(
        username="adjusted_limit_user",
        system_ip="Unassigned",
        is_valid=True,
    )
    online_device = AgentDevice(system_id="sys-adjusted-balance", status="approved", secure_token="tok")
    db_session.add_all([user, online_device])
    db_session.flush()

    schedule = UserWeeklySchedule(user_id=user.id, is_synced=True)
    weekday_columns = (
        'monday_hours',
        'tuesday_hours',
        'wednesday_hours',
        'thursday_hours',
        'friday_hours',
        'saturday_hours',
        'sunday_hours',
    )
    setattr(schedule, weekday_columns[date.today().weekday()], 1.0)
    db_session.add(schedule)

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id="sys-adjusted-balance",
        linux_username="adjusted_limit_user",
        is_valid=True,
    )
    db_session.add(mapping)
    user.apply_daily_limit_adjustment('+', 300, date.today())
    db_session.commit()

    ws = StatefulTimeWS(time_spent=600, time_left=3000)
    AgentConnectionManager.register("sys-adjusted-balance", ws, "10.0.0.6")

    with app.app_context():
        manager._update_user_data()

    rebalance_calls = [
        json.loads(message)
        for message in ws.sent_messages
        if json.loads(message).get("action") == "modify_time_left"
    ]
    assert rebalance_calls
    assert rebalance_calls[-1]["args"] == {"operation": "+", "seconds": 300}

    refreshed_user = ManagedUser.query.filter_by(id=user.id).first()
    assert refreshed_user.get_config_value("TIME_LEFT_DAY") == 3300

    AgentConnectionManager.unregister("sys-adjusted-balance")


def test_task_manager_failures_and_threads(app, db_session):
    manager = BackgroundTaskManager(app)

    user = ManagedUser(
        username="fail_user",
        system_ip="Unassigned",
        is_valid=True,
        pending_time_adjustment=100,
        pending_time_operation="+"
    )
    device = AgentDevice(system_id="sys-fail", status="approved", secure_token="tok")
    db_session.add_all([user, device])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id="sys-fail",
        linux_username="fail_user",
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()

    schedule = UserWeeklySchedule(user_id=user.id, monday_hours=2.0, is_synced=False)
    interval = UserDailyTimeInterval(
        user_id=user.id,
        day_of_week=1,
        start_hour=9,
        start_minute=0,
        end_hour=17,
        end_minute=0,
        is_synced=False
    )
    db_session.add_all([schedule, interval])
    db_session.commit()

    class FailedWS:
        def send(self, message):
            payload = json.loads(message)
            correlation_id = payload.get("correlation_id")
            AgentConnectionManager.route_response(correlation_id, {
                "success": False,
                "message": "Command Failed",
                "data": None
            })

    AgentConnectionManager.register("sys-fail", FailedWS(), "10.0.0.3")

    with app.app_context():
        manager._update_user_data()

    assert user.pending_time_adjustment == 100
    assert not schedule.is_synced
    assert not interval.is_synced

    AgentConnectionManager.unregister("sys-fail")

    from unittest.mock import patch

    # 2. Hitting loop exceptions and app=None with time.sleep patched to terminate the infinite loop
    manager_no_app = BackgroundTaskManager()
    manager_no_app.running = True
    with patch('time.sleep', side_effect=lambda s: setattr(manager_no_app, 'running', False)):
        manager_no_app._run_tasks()

    # Mock the entire lock to throw Exception on acquire
    mock_lock_fail = MagicMock()
    mock_lock_fail.acquire.side_effect = Exception("Lock error")
    with patch.object(manager, '_task_lock', new=mock_lock_fail), \
         patch('time.sleep', side_effect=lambda s: setattr(manager, 'running', False)):
        manager.running = True
        manager._run_tasks()
        assert manager.last_error is not None

    # Mock the entire lock to return False on acquire (already locked)
    mock_lock_locked = MagicMock()
    mock_lock_locked.acquire.return_value = False
    with patch.object(manager, '_task_lock', new=mock_lock_locked), \
         patch('time.sleep', side_effect=lambda s: setattr(manager, 'running', False)):
        manager.running = True
        manager._run_tasks()

    # 3. Thread graceful exit and timeout logs
    manager.start()
    manager.stop()


def test_task_manager_syncs_domain_policy_payloads(app, db_session):
    manager = BackgroundTaskManager(app)

    user = ManagedUser(username="policy-user", system_ip="Unassigned", is_valid=True)
    device = AgentDevice(system_id="sys-policy", status="approved", secure_token="tok")
    source = BlocklistSource(name="DoH", source_type=BlocklistSource.TYPE_MANUAL, is_enabled=True)
    db_session.add_all([user, device, source])
    db_session.flush()
    db_session.add_all([
        ManagedUserDeviceMap(
            managed_user_id=user.id,
            system_id=device.system_id,
            linux_username="policy-user",
            linux_uid=1005,
            is_valid=True,
        ),
        ManagedUserBlocklistAssignment(managed_user_id=user.id, source_id=source.id),
        BlocklistDomain(source_id=source.id, domain="dns.google"),
        BlocklistDomain(source_id=source.id, domain="cloudflare-dns.com"),
    ])
    db_session.commit()

    ws = DummyWS()
    AgentConnectionManager.register(device.system_id, ws, "10.0.0.20")

    with app.app_context():
        manager._sync_domain_policies()

    sent_payloads = [
        json.loads(message)
        for message in ws.sent_messages
    ]
    sent_actions = [payload["action"] for payload in sent_payloads]
    assert sent_actions == [
        "get_domain_policy_state",
        "begin_domain_policy_sync",
        "sync_domain_policy_chunk",
        "update_domain_policy_manifest",
        "finalize_domain_policy_sync",
    ]

    chunk_payload = next(
        payload for payload in sent_payloads
        if payload["action"] == "sync_domain_policy_chunk"
    )["args"]
    assert chunk_payload["source_id"] == "1"
    assert chunk_payload["domains"] == ["cloudflare-dns.com", "dns.google"]

    manifest_payload = next(
        payload for payload in sent_payloads
        if payload["action"] == "update_domain_policy_manifest"
    )["args"]
    assert manifest_payload["policies"]["1005"]["linux_username"] == "policy-user"
    assert manifest_payload["policies"]["1005"]["source_ids"] == ["1"]

    mapping = ManagedUserDeviceMap.query.filter_by(system_id=device.system_id).first()
    assert mapping.blocklist_is_synced
    assert mapping.blocklist_policy_hash
    assert mapping.blocklist_last_synced is not None

    sent_count = len(ws.sent_messages)
    with app.app_context():
        manager._sync_domain_policies()
    assert len(ws.sent_messages) == sent_count

    AgentConnectionManager.unregister(device.system_id)


def test_task_manager_syncs_large_domain_sources_in_multiple_chunks(app, db_session):
    manager = BackgroundTaskManager(app)

    user = ManagedUser(username="large-policy-user", system_ip="Unassigned", is_valid=True)
    device = AgentDevice(system_id="sys-policy-large", status="approved", secure_token="tok")
    source = BlocklistSource(name="Large DoH", source_type=BlocklistSource.TYPE_MANUAL, is_enabled=True)
    db_session.add_all([user, device, source])
    db_session.flush()
    db_session.add(
        ManagedUserDeviceMap(
            managed_user_id=user.id,
            system_id=device.system_id,
            linux_username="large-policy-user",
            linux_uid=1010,
            is_valid=True,
        )
    )
    db_session.add(ManagedUserBlocklistAssignment(managed_user_id=user.id, source_id=source.id))
    db_session.add_all([
        BlocklistDomain(source_id=source.id, domain=f"domain-{index:04d}.example.com")
        for index in range(1003)
    ])
    db_session.commit()

    ws = DummyWS()
    AgentConnectionManager.register(device.system_id, ws, "10.0.0.21")

    with app.app_context():
        manager._sync_domain_policies()

    chunk_payloads = [
        json.loads(message)["args"]
        for message in ws.sent_messages
        if json.loads(message).get("action") == "sync_domain_policy_chunk"
    ]
    assert len(chunk_payloads) == 2
    assert len(chunk_payloads[0]["domains"]) == 1000
    assert len(chunk_payloads[1]["domains"]) == 3
    assert chunk_payloads[0]["domains"][0] == "domain-0000.example.com"
    assert chunk_payloads[1]["domains"][-1] == "domain-1002.example.com"

    AgentConnectionManager.unregister(device.system_id)


def test_task_manager_backoffs_repeated_domain_policy_failures(app, db_session):
    manager = BackgroundTaskManager(app)

    user = ManagedUser(username="retry-policy-user", system_ip="Unassigned", is_valid=True)
    device = AgentDevice(system_id="sys-policy-retry", status="approved", secure_token="tok")
    source = BlocklistSource(name="Retry DoH", source_type=BlocklistSource.TYPE_MANUAL, is_enabled=True)
    db_session.add_all([user, device, source])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=device.system_id,
        linux_username="retry-policy-user",
        linux_uid=1020,
        is_valid=True,
    )
    db_session.add_all([
        mapping,
        ManagedUserBlocklistAssignment(managed_user_id=user.id, source_id=source.id),
        BlocklistDomain(source_id=source.id, domain="dns.google"),
    ])
    db_session.commit()

    class FailingWS:
        def __init__(self):
            self.sent_messages = []

        def send(self, message):
            self.sent_messages.append(message)
            payload = json.loads(message)
            correlation_id = payload.get("correlation_id")
            if not correlation_id:
                return
            action = payload.get("action")
            if action == "get_domain_policy_state":
                AgentConnectionManager.route_response(correlation_id, {
                    "success": False,
                    "message": "agent unavailable for policy sync",
                    "data": {},
                })
            else:
                AgentConnectionManager.route_response(correlation_id, {
                    "success": True,
                    "message": "Success",
                    "data": {},
                })

    ws = FailingWS()
    AgentConnectionManager.register(device.system_id, ws, "10.0.0.22")

    with app.app_context():
        manager._sync_domain_policies()

    first_sent_count = len(ws.sent_messages)
    assert first_sent_count == 1

    with app.app_context():
        manager._sync_domain_policies()

    assert len(ws.sent_messages) == first_sent_count

    db_session.refresh(mapping)
    mapping.blocklist_last_attempted = datetime.utcnow() - timedelta(hours=5)
    db_session.commit()

    with app.app_context():
        manager._sync_domain_policies()

    assert len(ws.sent_messages) == first_sent_count + 1
    AgentConnectionManager.unregister(device.system_id)


def test_task_manager_retries_immediately_when_policy_hash_changes(app, db_session):
    manager = BackgroundTaskManager(app)

    user = ManagedUser(username="changed-policy-user", system_ip="Unassigned", is_valid=True)
    device = AgentDevice(system_id="sys-policy-change", status="approved", secure_token="tok")
    source = BlocklistSource(name="Change DoH", source_type=BlocklistSource.TYPE_MANUAL, is_enabled=True)
    db_session.add_all([user, device, source])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=device.system_id,
        linux_username="changed-policy-user",
        linux_uid=1030,
        is_valid=True,
    )
    db_session.add_all([
        mapping,
        ManagedUserBlocklistAssignment(managed_user_id=user.id, source_id=source.id),
        BlocklistDomain(source_id=source.id, domain="dns.google"),
    ])
    db_session.commit()

    class FailingWS:
        def __init__(self):
            self.sent_messages = []

        def send(self, message):
            self.sent_messages.append(message)
            payload = json.loads(message)
            correlation_id = payload.get("correlation_id")
            if not correlation_id:
                return
            AgentConnectionManager.route_response(correlation_id, {
                "success": False,
                "message": "agent unavailable for policy sync",
                "data": {},
            })

    ws = FailingWS()
    AgentConnectionManager.register(device.system_id, ws, "10.0.0.23")

    with app.app_context():
        manager._sync_domain_policies()
    first_sent_count = len(ws.sent_messages)
    assert first_sent_count == 1

    db_session.refresh(source)
    source.content_revision = "forced-new-revision"
    db_session.commit()

    with app.app_context():
        manager._sync_domain_policies()

    assert len(ws.sent_messages) == first_sent_count + 1
    AgentConnectionManager.unregister(device.system_id)


def test_task_manager_refreshes_external_blocklists_in_chunks(app, db_session):
    manager = BackgroundTaskManager(app)
    source = BlocklistSource(
        name="streamed-list",
        source_type=BlocklistSource.TYPE_EXTERNAL_URL,
        source_url="https://example.test/blocklist.txt",
        is_enabled=True,
    )
    db_session.add(source)
    db_session.flush()
    db_session.add(BlocklistDomain(source_id=source.id, domain="old.example"))
    db_session.commit()

    class StreamingResponse:
        status_code = 200
        headers = {
            'ETag': 'etag-123',
            'Last-Modified': 'Tue, 26 May 2026 00:00:00 GMT',
        }
        encoding = 'utf-8'

        def __init__(self):
            self.closed = False

        def iter_content(self, chunk_size=1):
            del chunk_size
            yield b'dns.go'
            yield b'ogle\n# comment\ncloud'
            yield b'flare-dns.com\ninvalid entry\n'
            yield b'dns.google\n'

        def close(self):
            self.closed = True

    response = StreamingResponse()

    from unittest.mock import patch

    with app.app_context(), patch('src.task_manager.requests.get', return_value=response):
        success, message = manager.refresh_external_blocklist_source(source.id, force=True)

    assert success
    assert 'with 2 domain(s)' in message
    db_session.expire_all()
    refreshed_source = BlocklistSource.query.get(source.id)
    refreshed_domains = [
        row.domain
        for row in BlocklistDomain.query.filter_by(source_id=source.id).order_by(BlocklistDomain.domain.asc()).all()
    ]
    assert refreshed_domains == ['cloudflare-dns.com', 'dns.google']
    assert refreshed_source.etag == 'etag-123'
    assert refreshed_source.source_last_modified == 'Tue, 26 May 2026 00:00:00 GMT'
    assert 'Domain must not contain whitespace' in refreshed_source.last_sync_error
    assert response.closed


def test_task_manager_delivers_pending_alerts(app, db_session):
    manager = BackgroundTaskManager(app)
    device = AgentDevice(
        system_id="alert-device",
        system_hostname="family-pc",
        status="approved",
        secure_token="tok",
    )
    db_session.add(device)
    db_session.flush()

    alert = AgentAlert(
        system_id=device.system_id,
        event_type="system_startup",
        linux_username="alice",
        occurred_at=datetime.utcnow(),
        payload_json='{"system_id":"alert-device","event_type":"system_startup","details":{"source":"test"}}',
        webhook_enabled_snapshot=True,
        delivery_status=AgentAlert.DELIVERY_PENDING,
    )
    db_session.add(alert)
    db_session.commit()

    Settings.set_value('alert_webhook_enabled', '1')
    Settings.set_value('alert_webhook_url', 'https://hooks.example.test/timekpr')
    Settings.set_value('alert_webhook_secret', 'shared-secret')

    class DummyResponse:
        status_code = 204
        text = ''

    from unittest.mock import patch

    with app.app_context(), patch('src.task_manager.requests.post', return_value=DummyResponse()) as mock_post:
        manager._deliver_pending_alerts()

    delivered_alert = AgentAlert.query.get(alert.id)
    assert delivered_alert.delivery_status == AgentAlert.DELIVERY_DELIVERED
    assert delivered_alert.delivery_attempts == 1
    assert delivered_alert.delivered_at is not None
    assert mock_post.called
    _, kwargs = mock_post.call_args
    assert kwargs['headers']['X-Timekpr-Alert-Id'] == str(alert.id)
    assert kwargs['headers']['X-Timekpr-Signature'].startswith('sha256=')
    assert '"system_hostname": "family-pc"' in kwargs['data']


def test_task_manager_retries_failed_alert_deliveries(app, db_session):
    manager = BackgroundTaskManager(app)
    device = AgentDevice(system_id="alert-retry-device", status="approved", secure_token="tok")
    db_session.add(device)
    db_session.flush()

    retry_alert = AgentAlert(
        system_id=device.system_id,
        event_type="system_restart",
        occurred_at=datetime.utcnow(),
        payload_json='{"system_id":"alert-retry-device","event_type":"system_restart","details":{}}',
        webhook_enabled_snapshot=True,
        delivery_status=AgentAlert.DELIVERY_PENDING,
    )
    disabled_alert = AgentAlert(
        system_id=device.system_id,
        event_type="system_sleep",
        occurred_at=datetime.utcnow(),
        payload_json='{"system_id":"alert-retry-device","event_type":"system_sleep","details":{}}',
        webhook_enabled_snapshot=False,
        delivery_status=AgentAlert.DELIVERY_DISABLED,
    )
    db_session.add_all([retry_alert, disabled_alert])
    db_session.commit()

    Settings.set_value('alert_webhook_enabled', '1')
    Settings.set_value('alert_webhook_url', 'https://hooks.example.test/timekpr')

    from unittest.mock import patch

    with app.app_context(), patch('src.task_manager.requests.post', side_effect=RuntimeError('boom')):
        manager._deliver_pending_alerts()

    refreshed_retry = AgentAlert.query.get(retry_alert.id)
    assert refreshed_retry.delivery_status == AgentAlert.DELIVERY_RETRYING
    assert refreshed_retry.delivery_attempts == 1
    assert 'boom' in refreshed_retry.last_delivery_error

    Settings.set_value('alert_webhook_enabled', '0')
    with app.app_context(), patch('src.task_manager.requests.post') as mock_post:
        manager._deliver_pending_alerts()
    assert not mock_post.called

    refreshed_disabled = AgentAlert.query.get(disabled_alert.id)
    assert refreshed_disabled.delivery_status == AgentAlert.DELIVERY_DISABLED
