import pytest
import json
from datetime import datetime, date, timedelta
from src.task_manager import BackgroundTaskManager
from src.database import (
    ManagedUser,
    ManagedUserDeviceMap,
    UserTimeUsage,
    UserWeeklySchedule,
    UserDailyTimeInterval,
    AgentDevice,
    db,
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
                # Default response
                AgentConnectionManager.route_response(correlation_id, {
                    "success": True,
                    "message": "Success",
                    "data": {"stdout": "TIME_SPENT_DAY: 450\n"}
                })
        except Exception:
            pass

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
        start_hour=9,
        start_minute=0,
        end_hour=17,
        end_minute=0,
        is_synced=False
    )
    db_session.add(interval)
    db_session.commit()

    with app.app_context():
        manager._update_user_data()

    assert interval.is_synced

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
