"""Regression tests for UTC day-boundary screen time rollover."""

from datetime import datetime, timedelta, timezone

from src.task_manager import BackgroundTaskManager
from src.users_manager import _refresh_managed_user_summary
from src.database import (
    AgentDevice,
    ManagedUser,
    ManagedUserDeviceMap,
    UserTimeUsage,
    USAGE_SNAPSHOT_DATE_KEY,
    ensure_offline_mapping_day_snapshot,
    get_mapping_time_spent_for_day,
    mapping_usage_is_for_day,
    stamp_usage_snapshot,
    utc_today,
)


def test_offline_mapping_does_not_restore_stale_usage_after_last_checked_bump(app, db_session):
    manager = BackgroundTaskManager(app)
    user = ManagedUser(username="rollover_user", system_ip="Unassigned", is_valid=True)
    device = AgentDevice(system_id="sys-offline-rollover", status="approved", secure_token="tok")
    db_session.add_all([user, device])
    db_session.flush()

    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=device.system_id,
        linux_username="rollover_user",
        is_valid=True,
        last_checked=yesterday,
        last_config='{"TIME_SPENT_DAY": 9360, "TIME_LEFT_DAY": 0}',
    )
    db_session.add(mapping)
    db_session.commit()

    today = utc_today()
    with app.app_context():
        manager._update_user_data()
        usage1 = UserTimeUsage.query.filter_by(user_id=user.id, date=today).first()
        assert usage1 is not None
        assert usage1.time_spent == 0

        manager._update_user_data()
        usage2 = UserTimeUsage.query.filter_by(user_id=user.id, date=today).first()
        assert usage2.time_spent == 0

        _refresh_managed_user_summary(user)
        db_session.commit()
        usage3 = UserTimeUsage.query.filter_by(user_id=user.id, date=today).first()
        assert usage3.time_spent == 0


def test_usage_snapshot_date_guards_stale_offline_config(db_session):
    device = AgentDevice(system_id="dev-snapshot", status="approved", secure_token="token")
    user = ManagedUser(username="snapshot_user", system_ip="Unassigned", is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()

    yesterday = (utc_today() - timedelta(days=1)).isoformat()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=device.system_id,
        linux_username="snapshot_user",
        is_valid=True,
        last_checked=datetime.now(timezone.utc),
        last_config=(
            '{"TIME_SPENT_DAY": 7200, "TIME_LEFT_DAY": 600, '
            f'"{USAGE_SNAPSHOT_DATE_KEY}": "{yesterday}"}}'
        ),
    )
    db_session.add(mapping)
    db_session.commit()

    assert get_mapping_time_spent_for_day(mapping) == 0
    assert not mapping_usage_is_for_day(mapping)


def test_ensure_offline_mapping_day_snapshot_resets_stale_usage(db_session):
    device = AgentDevice(system_id="dev-reset", status="approved", secure_token="token")
    user = ManagedUser(username="reset_user", system_ip="Unassigned", is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=device.system_id,
        linux_username="reset_user",
        is_valid=True,
        last_checked=datetime.now(timezone.utc) - timedelta(days=1),
        last_config='{"TIME_SPENT_DAY": 3600, "TIME_LEFT_DAY": 0}',
    )
    db_session.add(mapping)
    db_session.commit()

    changed = ensure_offline_mapping_day_snapshot(mapping, utc_today(), 7200)
    assert changed
    assert get_mapping_time_spent_for_day(mapping) == 0
    assert mapping.get_config_value("TIME_LEFT_DAY") == 7200
    assert mapping.get_config_value(USAGE_SNAPSHOT_DATE_KEY) == utc_today().isoformat()


def test_stamp_usage_snapshot_adds_utc_day(db_session):
    stamped = stamp_usage_snapshot({"TIME_SPENT_DAY": 120}, utc_today())
    assert stamped[USAGE_SNAPSHOT_DATE_KEY] == utc_today().isoformat()
