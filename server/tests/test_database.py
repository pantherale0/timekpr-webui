from datetime import datetime, timedelta, timezone
from src.database import (
    coerce_time_left_day,
    coerce_time_spent_day,
    get_mapping_time_spent_for_day,
    get_mapping_time_left_for_day,
    Settings,
    AgentAlert,
    AgentDevice,
    ManagedUser,
    ManagedUserDeviceMap,
    UserTimeUsage,
    UserWeeklySchedule,
    UserDailyTimeInterval,
)

def test_coerce_time_spent_day():
    assert coerce_time_spent_day(None) == 0
    assert coerce_time_spent_day(True) == 0
    assert coerce_time_spent_day(False) == 0
    assert coerce_time_spent_day(42) == 42
    assert coerce_time_spent_day([10, 20]) == 10
    assert coerce_time_spent_day([]) == 0
    assert coerce_time_spent_day(" 123 \n") == 123
    assert coerce_time_spent_day("invalid") == 0


def test_coerce_time_left_day():
    assert coerce_time_left_day(None) is None
    assert coerce_time_left_day(True) is None
    assert coerce_time_left_day(42) == 42
    assert coerce_time_left_day([10, 20]) == 10
    assert coerce_time_left_day(" 123 \n") == 123
    assert coerce_time_left_day("invalid") is None


def test_get_mapping_time_spent_for_day_uses_same_day_snapshot(db_session):
    device = AgentDevice(system_id="dev-day-check", status="approved", secure_token="token")
    user = ManagedUser(username="same_day_user", system_ip="Unassigned", is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=device.system_id,
        linux_username="same_day_user",
        is_valid=True,
        last_checked=datetime.now(timezone.utc),
        last_config='{"TIME_SPENT_DAY": 900}',
    )
    db_session.add(mapping)
    db_session.commit()

    today = datetime.now(timezone.utc).date()
    assert get_mapping_time_spent_for_day(mapping, today) == 900

    mapping.last_checked = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()
    assert get_mapping_time_spent_for_day(mapping, today) == 0


def test_get_mapping_time_left_for_day_uses_same_day_snapshot(db_session):
    device = AgentDevice(system_id="dev-left-check", status="approved", secure_token="token")
    user = ManagedUser(username="left_day_user", system_ip="Unassigned", is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=device.system_id,
        linux_username="left_day_user",
        is_valid=True,
        last_checked=datetime.now(timezone.utc),
        last_config='{"TIME_LEFT_DAY": 1500}',
    )
    db_session.add(mapping)
    db_session.commit()

    today = datetime.now(timezone.utc).date()
    assert get_mapping_time_left_for_day(mapping, today) == 1500

    mapping.last_checked = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()
    assert get_mapping_time_left_for_day(mapping, today) is None

def test_settings_model(db_session):
    # Test setting and getting values
    Settings.set_value("test_key", "test_value")
    assert Settings.get_value("test_key") == "test_value"
    assert Settings.get_value("nonexistent", "default") == "default"

    # Test update existing
    Settings.set_value("test_key", "new_value")
    assert Settings.get_value("test_key") == "new_value"

    # Test password hashing and verification
    pw = "mysecretpassword"
    hashed = Settings.hash_password(pw)
    assert Settings.check_password(pw, hashed)
    assert not Settings.check_password("wrong", hashed)

    # Test set/check admin password
    Settings.set_admin_password("admin123")
    assert Settings.check_admin_password("admin123")
    assert not Settings.check_admin_password("wrong")

    # Test password migration (from plain text to hashed)
    # Simulate old plain text password in DB
    plain_password_setting = Settings(key="admin_password", value="oldplain")
    db_session.add(plain_password_setting)
    # Remove hashed password setting to trigger migration fallback
    h = Settings.query.filter_by(key="admin_password_hash").first()
    if h:
        db_session.delete(h)
    db_session.commit()

    assert Settings.check_admin_password("oldplain")
    # Verify migration occurred
    assert Settings.get_value("admin_password") is None
    assert Settings.get_value("admin_password_hash") is not None

    # Test fallback to default when no password is set at all
    db_session.delete(Settings.query.filter_by(key="admin_password_hash").first())
    db_session.commit()
    assert Settings.check_admin_password("admin")

def test_agent_device_model(db_session):
    device = AgentDevice(
        system_id="dev-123ab",
        system_hostname="family-pc",
        system_ip="192.168.1.100",
        status="pending",
        secure_token="token-xyz"
    )
    db_session.add(device)
    db_session.commit()

    fallback_device = AgentDevice(system_id="dev-456", status="approved")
    db_session.add(fallback_device)
    db_session.commit()

    assert repr(device) == "<AgentDevice dev-123ab [pending]>"
    assert device.system_id == "dev-123ab"
    assert device.display_name == "family-pc"
    assert device.system_id_suffix == "ab"
    assert device.format_display_name(include_suffix=True) == "family-pc (ab)"
    assert fallback_device.display_name == "dev-456"
    assert fallback_device.format_display_name(include_suffix=True) == "dev-456"


def test_agent_alert_model(db_session):
    device = AgentDevice(system_id="dev-alert", status="approved", secure_token="token")
    db_session.add(device)
    db_session.commit()

    alert = AgentAlert(
        system_id=device.system_id,
        event_type="system_startup",
        linux_username="alice",
        occurred_at=datetime.now(timezone.utc),
        payload_json='{"system_id":"dev-alert","event_type":"system_startup","details":{"source":"test"}}',
        webhook_enabled_snapshot=True,
        delivery_status=AgentAlert.DELIVERY_PENDING,
    )
    db_session.add(alert)
    db_session.commit()

    assert repr(alert) == "<AgentAlert system_startup on dev-alert>"
    assert alert.payload["details"]["source"] == "test"
    assert alert.should_attempt_delivery

    alert.mark_delivery_attempt()
    assert alert.delivery_attempts == 1
    assert alert.last_delivery_attempt_at is not None

    alert.mark_retry("timeout")
    assert alert.delivery_status == AgentAlert.DELIVERY_RETRYING
    assert alert.last_delivery_error == "timeout"
    assert alert.should_attempt_delivery

    alert.mark_delivered()
    assert alert.delivery_status == AgentAlert.DELIVERY_DELIVERED
    assert alert.delivered_at is not None
    assert alert.last_delivery_error is None
    assert not alert.should_attempt_delivery

    alert.mark_delivery_disabled()
    assert alert.delivery_status == AgentAlert.DELIVERY_DISABLED
    assert alert.delivered_at is None

def test_managed_user_and_usage(db_session):
    device = AgentDevice(system_id="dev-123", status="approved", secure_token="token")
    db_session.add(device)
    db_session.commit()

    user = ManagedUser(
        username="john",
        system_ip="Unassigned",
        is_valid=True,
        last_config='{"TIME_SPENT_DAY": 1800, "LIMIT": 3600}'
    )
    db_session.add(user)
    db_session.flush()

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id="dev-123",
        linux_username="john",
        is_valid=True,
        last_config='{"TIME_SPENT_DAY": 1800, "LIMIT": 3600, "LINUX_UID": 1000}'
    )
    db_session.add(mapping)
    db_session.commit()

    assert repr(user) == "<ManagedUser john>"
    assert mapping.device == device
    assert mapping.managed_user == user
    assert mapping.get_config_value("LINUX_UID") == 1000
    assert user.get_config_value("TIME_SPENT_DAY") == 1800
    assert user.get_config_value("nonexistent") is None

    today = datetime.now(timezone.utc).date()
    schedule = UserWeeklySchedule(user_id=user.id)
    weekday_columns = (
        'monday_hours',
        'tuesday_hours',
        'wednesday_hours',
        'thursday_hours',
        'friday_hours',
        'saturday_hours',
        'sunday_hours',
    )
    setattr(schedule, weekday_columns[today.weekday()], 2.0)
    db_session.add(schedule)
    db_session.commit()

    assert schedule.get_limit_seconds_for_day(today) == 7200
    assert user.get_effective_daily_limit_seconds(today) == 7200
    user.apply_daily_limit_adjustment('+', 300, today)
    assert user.get_daily_limit_adjustment_seconds(today) == 300
    assert user.get_effective_daily_limit_seconds(today) == 7500
    user.apply_daily_limit_adjustment('-', 120, today)
    assert user.get_daily_limit_adjustment_seconds(today) == 180
    assert user.get_effective_daily_limit_seconds(today) == 7380
    
    # Test invalid config JSON
    user.last_config = "{invalid_json}"
    db_session.commit()
    assert user.get_config_value("ANY") is None
    user.last_config = None
    assert user.get_config_value("ANY") is None

    # Test usage data
    usage1 = UserTimeUsage(user_id=user.id, date=today, time_spent=1200)
    usage2 = UserTimeUsage(user_id=user.id, date=today - timedelta(days=1), time_spent=2400)
    db_session.add_all([usage1, usage2])
    db_session.commit()

    assert repr(usage1) == f"<UserTimeUsage john {today}: 1200>"

    # Test get_recent_usage
    recent = user.get_recent_usage(days=3)
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    assert recent[today_str] == 1200
    assert recent[yesterday_str] == 2400
    assert recent[(today - timedelta(days=2)).strftime("%Y-%m-%d")] == 0

    # Test get_usage_weekly_grouped
    weekly = user.get_usage_weekly_grouped(weeks=2)
    assert len(weekly) == 2
    assert sum(w["total"] for w in weekly) == 3600

    # Test get_usage_monthly_grouped
    monthly = user.get_usage_monthly_grouped(months=2)
    assert len(monthly) == 2
    assert sum(m["total"] for m in monthly) == 3600

    # Test get_all_usage_monthly
    all_monthly = user.get_all_usage_monthly()
    assert len(all_monthly) in (1, 2)
    assert sum(m["total"] for m in all_monthly) == 3600

    # Test empty usages for grouped helpers
    db_session.delete(usage1)
    db_session.delete(usage2)
    db_session.commit()
    assert user.get_all_usage_monthly() == []

def test_user_weekly_schedule(db_session):
    user = ManagedUser(username="test_user", system_ip="Unassigned")
    db_session.add(user)
    db_session.commit()

    schedule = UserWeeklySchedule(user_id=user.id)
    db_session.add(schedule)
    db_session.commit()

    assert repr(schedule) == "<UserWeeklySchedule test_user>"

    # test get_schedule_dict
    s_dict = schedule.get_schedule_dict()
    assert s_dict["monday"] == 0.0

    # test set_schedule_from_dict
    schedule.set_schedule_from_dict({
        "monday": 2.5,
        "tuesday": 3.0
    })
    assert schedule.monday_hours == 2.5
    assert schedule.tuesday_hours == 3.0
    assert schedule.wednesday_hours == 0.0
    assert not schedule.is_synced

    # test set_weekdays_hours
    schedule.set_weekdays_hours(4.0)
    assert schedule.monday_hours == 4.0
    assert schedule.friday_hours == 4.0
    assert schedule.saturday_hours == 0.0

    # test has_pending_changes & mark_synced
    assert schedule.has_pending_changes()
    schedule.mark_synced()
    assert not schedule.has_pending_changes()
    assert schedule.last_synced is not None

def test_user_daily_time_interval(db_session):
    user = ManagedUser(username="test_user", system_ip="Unassigned")
    db_session.add(user)
    db_session.commit()

    interval = UserDailyTimeInterval(
        user_id=user.id,
        day_of_week=1,
        start_hour=9,
        start_minute=30,
        end_hour=17,
        end_minute=0
    )
    db_session.add(interval)
    db_session.commit()

    assert repr(interval) == "<UserDailyTimeInterval test_user Day1 09:30-17:00>"
    assert interval.get_time_range_string() == "09:30-17:00"
    assert interval.get_day_name() == "Monday"

    # test get_day_name bounds
    interval.day_of_week = 8
    assert interval.get_day_name() == "Unknown"
    interval.day_of_week = 1

    assert interval.is_valid_interval()
    assert interval.is_valid_interval(step_minutes=15)
    interval.start_hour = 18
    assert not interval.is_valid_interval()
    interval.start_hour = 9
    interval.start_minute = 10
    assert not interval.is_valid_interval(step_minutes=15)
    interval.start_minute = 30

    # Sync helpers
    interval.mark_synced()
    assert interval.is_synced
    interval.mark_modified()
    assert not interval.is_synced

    # to_timekpr_format
    # Case: is_enabled = False
    interval.is_enabled = False
    assert interval.to_timekpr_format() is None
    interval.is_enabled = True

    # Case: full hour range (minutes are 0)
    interval.start_minute = 0
    interval.end_minute = 0
    assert interval.to_timekpr_format() == ["9", "10", "11", "12", "13", "14", "15", "16"]

    # Case: partial hours in multiple hours range
    interval.start_minute = 30
    interval.end_minute = 15
    assert interval.to_timekpr_format() == ["9[30-59]", "10", "11", "12", "13", "14", "15", "16", "17[0-15]"]

    # Case: same hour partial range
    interval.start_hour = 9
    interval.end_hour = 9
    interval.start_minute = 15
    interval.end_minute = 45
    assert interval.to_timekpr_format() == ["9[15-45]"]

def test_user_daily_time_interval_collection_validation(db_session):
    user = ManagedUser(username="collection_user", system_ip="Unassigned")
    db_session.add(user)
    db_session.commit()

    morning = UserDailyTimeInterval(
        user_id=user.id,
        day_of_week=1,
        sort_order=0,
        start_hour=8,
        start_minute=0,
        end_hour=11,
        end_minute=0,
    )
    afternoon = UserDailyTimeInterval(
        user_id=user.id,
        day_of_week=1,
        sort_order=1,
        start_hour=15,
        start_minute=0,
        end_hour=17,
        end_minute=30,
    )
    overlap = UserDailyTimeInterval(
        user_id=user.id,
        day_of_week=1,
        sort_order=2,
        start_hour=10,
        start_minute=45,
        end_hour=12,
        end_minute=0,
    )

    assert UserDailyTimeInterval.validate_interval_collection(
        [morning, afternoon],
        step_minutes=15,
    )
    assert not UserDailyTimeInterval.validate_interval_collection(
        [morning, overlap],
        step_minutes=15,
    )

def test_database_model_missing_lines(db_session):
    user = ManagedUser(username="jack", system_ip="Unassigned")
    db_session.add(user)
    db_session.commit()

    interval = UserDailyTimeInterval(
        user_id=user.id,
        day_of_week=1,
        start_hour=9,
        start_minute=0,
        end_hour=17,
        end_minute=15
    )
    db_session.add(interval)
    db_session.commit()
    res = interval.to_timekpr_format()
    assert res == ["9", "10", "11", "12", "13", "14", "15", "16", "17[0-15]"]

    monthly = user.get_usage_monthly_grouped(months=24)
    assert len(monthly) == 24
