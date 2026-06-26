"""Persisted offline queue for agent commands."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError

from src.models import (
    AgentDevice,
    ManagedUserDeviceMap,
    PendingCommand,
    UserDailyTimeInterval,
    db,
)

_LOGGER = logging.getLogger(__name__)

MAX_QUEUE_DEPTH = 100
MAX_ATTEMPTS = 3

IMPERATIVE_ACTIONS = frozenset({
    'factory_reset',
    'unenroll',
    'refresh_installed_apps',
    'capture_screenshot',
})

POLICY_SNAPSHOT_ACTIONS = frozenset({
    'sync_linux_device_policy',
    'sync_android_device_policy',
    'sync_apparmor_policy',
    'sync_screenshot_policy',
    'set_weekly_time_limits',
    'set_allowed_hours',
})

DOMAIN_RECONCILE_ACTION = 'domain_policy_reconcile'

QUEUEABLE_ACTIONS = IMPERATIVE_ACTIONS | POLICY_SNAPSHOT_ACTIONS | {DOMAIN_RECONCILE_ACTION}

DEFAULT_TTL_HOURS = {
    'capture_screenshot': 24,
    'refresh_installed_apps': 24 * 7,
}

SUPERSEDE_COALESCE_ACTIONS = frozenset({
    'factory_reset',
    *POLICY_SNAPSHOT_ACTIONS,
    DOMAIN_RECONCILE_ACTION,
})


@dataclass
class FlushResult:
    delivered: int = 0
    failed: int = 0
    expired: int = 0
    skipped_offline: int = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_expired(expires_at: datetime | None) -> bool:
    normalized = _as_utc(expires_at)
    if normalized is None:
        return False
    return normalized < _utcnow()


def _normalize_username(username: str | None) -> str:
    return (username or '').strip()


def _build_coalesce_key(action: str, username: str | None) -> str | None:
    if action in SUPERSEDE_COALESCE_ACTIONS:
        if action == DOMAIN_RECONCILE_ACTION:
            return DOMAIN_RECONCILE_ACTION
        if action == 'factory_reset':
            return 'factory_reset:'
        return f'{action}:{_normalize_username(username)}'
    return None


def _command_kind_for_action(action: str) -> str:
    if action == DOMAIN_RECONCILE_ACTION:
        return PendingCommand.KIND_DOMAIN_RECONCILE
    if action in POLICY_SNAPSHOT_ACTIONS:
        return PendingCommand.KIND_POLICY_SNAPSHOT
    return PendingCommand.KIND_IMPERATIVE


def _pending_count(system_id: str) -> int:
    return PendingCommand.query.filter_by(
        system_id=system_id,
        status=PendingCommand.STATUS_PENDING,
    ).count()


def _supersede_pending_rows(system_id: str, coalesce_key: str | None) -> None:
    if not coalesce_key:
        return
    rows = PendingCommand.query.filter_by(
        system_id=system_id,
        coalesce_key=coalesce_key,
        status=PendingCommand.STATUS_PENDING,
    ).all()
    now = _utcnow()
    for row in rows:
        row.status = PendingCommand.STATUS_SUPERSEDED
        row.updated_at = now


def _expires_at_for_action(action: str) -> datetime | None:
    ttl_hours = DEFAULT_TTL_HOURS.get(action)
    if ttl_hours is None:
        return None
    return _utcnow() + timedelta(hours=ttl_hours)


def _serialize_args(args: dict | None) -> str | None:
    if not args:
        return None
    return json.dumps(args, sort_keys=True)


def _insert_pending_row(
    system_id: str,
    action: str,
    *,
    username: str | None = None,
    args: dict | None = None,
    command_kind: str | None = None,
) -> PendingCommand:
    if _pending_count(system_id) >= MAX_QUEUE_DEPTH:
        raise ValueError(f'Pending command queue is full for device {system_id}')

    coalesce_key = _build_coalesce_key(action, username)
    _supersede_pending_rows(system_id, coalesce_key)

    now = _utcnow()
    row = PendingCommand(
        system_id=system_id,
        action=action,
        username=_normalize_username(username) or None,
        command_kind=command_kind or _command_kind_for_action(action),
        args_json=_serialize_args(args),
        coalesce_key=coalesce_key,
        status=PendingCommand.STATUS_PENDING,
        created_at=now,
        updated_at=now,
        expires_at=_expires_at_for_action(action),
    )
    db.session.add(row)
    db.session.commit()
    return row


def enqueue_command(
    system_id: str,
    action: str,
    username: str | None = None,
    args: dict | None = None,
) -> PendingCommand:
    """Queue an imperative command with stored args."""
    return _insert_pending_row(
        system_id,
        action,
        username=username,
        args=args,
        command_kind=PendingCommand.KIND_IMPERATIVE,
    )


def enqueue_policy_snapshot(
    system_id: str,
    action: str,
    username: str | None = None,
) -> PendingCommand:
    """Queue a policy snapshot marker; payload is rebuilt at flush time."""
    if action not in POLICY_SNAPSHOT_ACTIONS:
        raise ValueError(f'Action {action} is not a policy snapshot command')
    return _insert_pending_row(
        system_id,
        action,
        username=username,
        args=None,
        command_kind=PendingCommand.KIND_POLICY_SNAPSHOT,
    )


def enqueue_domain_reconcile(system_id: str) -> PendingCommand:
    """Queue a domain-policy reconcile marker for a device."""
    return _insert_pending_row(
        system_id,
        DOMAIN_RECONCILE_ACTION,
        username=None,
        args=None,
        command_kind=PendingCommand.KIND_DOMAIN_RECONCILE,
    )


def get_pending_count(system_id: str) -> int:
    return _pending_count(system_id)


def _mapping_for_command(system_id: str, username: str | None) -> ManagedUserDeviceMap | None:
    normalized = _normalize_username(username)
    if not normalized:
        return None
    return ManagedUserDeviceMap.query.filter_by(
        system_id=system_id,
        linux_username=normalized,
    ).first()


def rebuild_command_args(row: PendingCommand) -> dict | None:
    """Rebuild flush-time args for policy snapshot commands."""
    from src.agent.helper import AgentClient

    action = row.action
    username = row.username or ''

    if action == 'sync_linux_device_policy':
        from src.policy.linux import build_device_policy_payload, get_or_create_policy

        mapping = _mapping_for_command(row.system_id, username)
        if mapping is None:
            return None
        policy = get_or_create_policy(mapping)
        return {'device_policy': build_device_policy_payload(policy)}

    if action == 'sync_android_device_policy':
        from src.policy.android import build_device_policy_payload, get_or_create_policy

        device = AgentDevice.query.get(row.system_id)
        if device is None:
            return None
        policy = get_or_create_policy(device)
        return {'device_policy': build_device_policy_payload(policy)}

    if action == 'sync_apparmor_policy':
        from src.user.approvals import build_full_app_policy_sync_payload

        mapping = _mapping_for_command(row.system_id, username)
        if mapping is None:
            return None
        policies_list, _, approval_policy = build_full_app_policy_sync_payload(mapping)
        payload = {'policies': policies_list or []}
        if approval_policy:
            payload['approval_policy'] = approval_policy
        return payload

    if action == 'sync_screenshot_policy':
        from src.device.screenshot_settings import (
            build_screenshot_policy_payload,
            get_or_create_settings,
        )

        device = AgentDevice.query.get(row.system_id)
        if device is None:
            return None
        settings = get_or_create_settings(device)
        return {'screenshot_policy': build_screenshot_policy_payload(settings)}

    if action == 'set_weekly_time_limits':
        mapping = _mapping_for_command(row.system_id, username)
        managed_user = mapping.managed_user if mapping is not None else None
        if managed_user is None or managed_user.weekly_schedule is None:
            return None
        return {'schedule': managed_user.weekly_schedule.get_schedule_dict()}

    if action == 'set_allowed_hours':
        mapping = _mapping_for_command(row.system_id, username)
        managed_user = mapping.managed_user if mapping is not None else None
        if managed_user is None:
            return None
        client = AgentClient(row.system_id)
        intervals_dict = {day: [] for day in range(1, 8)}
        for interval in sorted(
            managed_user.time_intervals,
            key=lambda item: (
                item.day_of_week,
                item.sort_order,
                item.start_total_minutes,
                item.id or 0,
            ),
        ):
            intervals_dict.setdefault(interval.day_of_week, []).append(interval)

        intervals_serial = {}
        for day_num in range(1, 8):
            day_intervals = intervals_dict.get(day_num) or []
            try:
                intervals_serial[str(day_num)] = client._build_dbus_day_hours(day_num, day_intervals)
            except ValueError:
                return None
        return {'intervals': intervals_serial}

    return row.args or None


def _mark_policy_synced(row: PendingCommand, success: bool, message: str | None = None) -> None:
    now = _utcnow()
    action = row.action
    username = row.username or ''

    if action == 'sync_linux_device_policy':
        from src.policy.linux import _get_policy_row

        mapping = _mapping_for_command(row.system_id, username)
        if mapping is None:
            return
        policy = _get_policy_row(mapping)
        if policy is None:
            return
        if success:
            policy.is_synced = True
            policy.last_synced_at = now
            policy.last_sync_error = None
        else:
            policy.is_synced = False
            policy.last_sync_error = (message or 'Sync failed')[:500]

    elif action == 'sync_android_device_policy':
        from src.policy.android import _get_policy_row

        device = AgentDevice.query.get(row.system_id)
        if device is None:
            return
        policy = _get_policy_row(device)
        if policy is None:
            return
        if success:
            policy.is_synced = True
            policy.last_synced_at = now
            policy.last_sync_error = None
        else:
            policy.is_synced = False
            policy.last_sync_error = (message or 'Sync failed')[:500]

    elif action == 'sync_screenshot_policy':
        device = AgentDevice.query.get(row.system_id)
        if device is None or device.screenshot_settings is None:
            return
        settings = device.screenshot_settings
        if success:
            settings.is_synced = True
            settings.last_synced_at = now
            settings.last_sync_error = None
        else:
            settings.is_synced = False
            settings.last_sync_error = (message or 'Sync failed')[:500]

    elif action == 'set_weekly_time_limits':
        mapping = _mapping_for_command(row.system_id, username)
        managed_user = mapping.managed_user if mapping is not None else None
        if managed_user is None or managed_user.weekly_schedule is None:
            return
        if success:
            managed_user.weekly_schedule.mark_synced()

    elif action == 'set_allowed_hours':
        mapping = _mapping_for_command(row.system_id, username)
        managed_user = mapping.managed_user if mapping is not None else None
        if managed_user is None:
            return
        if success:
            for interval in UserDailyTimeInterval.query.filter_by(
                user_id=managed_user.id,
                is_synced=False,
            ).all():
                interval.mark_synced()


def _execute_pending_row(row: PendingCommand) -> tuple[bool, str]:
    from src.agent.helper import AgentClient, AgentConnectionManager

    if not AgentConnectionManager.is_online(row.system_id):
        return False, 'Agent offline'

    if row.command_kind == PendingCommand.KIND_DOMAIN_RECONCILE:
        from app import task_manager

        try:
            task_manager._run_requested_domain_policy_sync(
                row.system_id,
                {},
                'pending_command_flush',
            )
            return True, 'Domain policy reconcile completed'
        except (RuntimeError, TypeError, ValueError, SQLAlchemyError) as exc:
            return False, str(exc)

    client = AgentClient(row.system_id)
    username = row.username or ''
    action = row.action

    if row.command_kind == PendingCommand.KIND_POLICY_SNAPSHOT:
        args = rebuild_command_args(row)
        if args is None:
            return False, f'Could not rebuild args for {action}'

        if action == 'sync_linux_device_policy':
            success, message = client.sync_linux_device_policy(username, args.get('device_policy', {}))
        elif action == 'sync_android_device_policy':
            success, message = client.sync_android_device_policy(username or 'system', args.get('device_policy', {}))
        elif action == 'sync_apparmor_policy':
            success, message = client.sync_apparmor_policy(
                username,
                args.get('policies', []),
                approval_policy=args.get('approval_policy'),
            )
        elif action == 'sync_screenshot_policy':
            success, message = client.sync_screenshot_policy(args.get('screenshot_policy', {}))
        elif action == 'set_weekly_time_limits':
            success, message = client.set_weekly_time_limits(username, args.get('schedule', {}))
        elif action == 'set_allowed_hours':
            success, message, _ = AgentConnectionManager.send_command_sync(
                row.system_id,
                action,
                username,
                {'intervals': args.get('intervals', {})},
                queue_if_offline=False,
            )
        else:
            return False, f'Unsupported policy snapshot action: {action}'

        _mark_policy_synced(row, success, message)
        return success, message or ''

    args = row.args or {}
    if action == 'factory_reset':
        success, message = client.factory_reset_device(username)
    elif action == 'unenroll':
        success, message = client.unenroll_device(username)
    elif action == 'refresh_installed_apps':
        try:
            client.refresh_installed_apps(username)
            return True, 'Installed apps refresh queued'
        except RuntimeError as exc:
            return False, str(exc)
    elif action == 'capture_screenshot':
        try:
            client.capture_screenshot(username or None)
            return True, 'Screenshot capture queued'
        except RuntimeError as exc:
            return False, str(exc)
    else:
        success, message, _ = AgentConnectionManager.send_command_sync(
            row.system_id,
            action,
            username,
            args,
            queue_if_offline=False,
        )
        return success, message or ''

    return success, message or ''


def _mark_row_completed(row: PendingCommand) -> None:
    row.status = PendingCommand.STATUS_COMPLETED
    row.updated_at = _utcnow()
    row.last_error = None


def _mark_row_failed(row: PendingCommand, message: str) -> None:
    row.attempt_count += 1
    row.updated_at = _utcnow()
    row.last_error = (message or 'Delivery failed')[:500]
    if row.attempt_count >= MAX_ATTEMPTS:
        row.status = PendingCommand.STATUS_FAILED
    else:
        row.status = PendingCommand.STATUS_PENDING


def _mark_row_expired(row: PendingCommand) -> None:
    row.status = PendingCommand.STATUS_EXPIRED
    row.updated_at = _utcnow()


def expire_stale_commands() -> int:
    """Mark expired pending commands and return how many were expired."""
    now = _utcnow()
    rows = PendingCommand.query.filter(
        PendingCommand.status == PendingCommand.STATUS_PENDING,
        PendingCommand.expires_at.isnot(None),
    ).all()
    expired_rows = [row for row in rows if _is_expired(row.expires_at)]
    for row in expired_rows:
        _mark_row_expired(row)
    if expired_rows:
        db.session.commit()
    return len(expired_rows)


def flush_pending_commands(system_id: str, *, app=None) -> FlushResult:
    """Deliver pending commands for a device in FIFO order."""
    from src.agent.helper import AgentConnectionManager

    result = FlushResult()
    if app is not None:
        ctx = app.app_context()
        ctx.push()

    try:
        expire_stale_commands()

        while AgentConnectionManager.is_online(system_id):
            row = (
                PendingCommand.query.filter_by(
                    system_id=system_id,
                    status=PendingCommand.STATUS_PENDING,
                )
                .order_by(PendingCommand.created_at.asc())
                .first()
            )
            if row is None:
                break

            if _is_expired(row.expires_at):
                _mark_row_expired(row)
                db.session.commit()
                result.expired += 1
                continue

            row.status = PendingCommand.STATUS_IN_FLIGHT
            row.updated_at = _utcnow()
            db.session.commit()

            try:
                success, message = _execute_pending_row(row)
            except (OSError, RuntimeError, SQLAlchemyError, TypeError, ValueError) as exc:
                success = False
                message = str(exc)

            if not AgentConnectionManager.is_online(system_id):
                row.status = PendingCommand.STATUS_PENDING
                row.updated_at = _utcnow()
                db.session.commit()
                result.skipped_offline += 1
                break

            if success:
                _mark_row_completed(row)
                if row.action == 'factory_reset':
                    device = AgentDevice.query.get(system_id)
                    if device is not None:
                        device.pending_factory_reset = False
                db.session.commit()
                result.delivered += 1
                _LOGGER.info(
                    'Delivered pending command %s (%s) to %s',
                    row.id,
                    row.action,
                    system_id,
                )
            else:
                _mark_row_failed(row, message)
                db.session.commit()
                result.failed += 1
                _LOGGER.warning(
                    'Pending command %s (%s) failed for %s: %s',
                    row.id,
                    row.action,
                    system_id,
                    message,
                )
    finally:
        if app is not None:
            ctx.pop()

    return result


def backfill_pending_factory_reset_commands() -> int:
    """Convert legacy pending_factory_reset flags into queued commands."""
    devices = AgentDevice.query.filter_by(pending_factory_reset=True).all()
    created = 0
    for device in devices:
        existing = PendingCommand.query.filter_by(
            system_id=device.system_id,
            action='factory_reset',
            status=PendingCommand.STATUS_PENDING,
        ).first()
        if existing is not None:
            device.pending_factory_reset = False
            continue
        try:
            enqueue_command(device.system_id, 'factory_reset', username=None, args={})
            device.pending_factory_reset = False
            created += 1
        except ValueError as exc:
            _LOGGER.warning(
                'Could not backfill factory reset command for %s: %s',
                device.system_id,
                exc,
            )
    if created:
        db.session.commit()
    return created


def queue_offline_command(
    system_id: str,
    action: str,
    username: str | None = None,
    args: dict | None = None,
) -> PendingCommand:
    """Enqueue a command based on action category."""
    if action in POLICY_SNAPSHOT_ACTIONS:
        return enqueue_policy_snapshot(system_id, action, username)
    if action == DOMAIN_RECONCILE_ACTION:
        return enqueue_domain_reconcile(system_id)
    return enqueue_command(system_id, action, username, args)
