"""Business logic for Linux device screenshot recall settings and agent sync."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from src.database import AgentDevice, DeviceRecallSettings, db

_LOGGER = logging.getLogger(__name__)


def _is_linux_device(device: AgentDevice) -> bool:
    platform = (device.platform or 'linux').strip().lower()
    return platform not in {'android', 'nintendo', 'xbox'}


def build_recall_policy_payload(settings: DeviceRecallSettings) -> dict:
    return {
        'enabled': bool(settings.enabled),
        'intervalSeconds': int(settings.interval_seconds),
    }


def compute_revision(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def get_or_create_settings(device: AgentDevice) -> DeviceRecallSettings:
    if not _is_linux_device(device):
        raise ValueError('Screenshot recall is only supported for Linux devices')

    settings = device.recall_settings
    if settings is None:
        settings = DeviceRecallSettings(
            system_id=device.system_id,
            enabled=False,
            interval_seconds=DeviceRecallSettings.DEFAULT_INTERVAL_SECONDS,
            retention_hours=DeviceRecallSettings.DEFAULT_RETENTION_HOURS,
        )
        payload = build_recall_policy_payload(settings)
        settings.revision = compute_revision(payload)
        db.session.add(settings)
        db.session.flush()
    return settings


def build_settings_summary(settings: DeviceRecallSettings, device: AgentDevice) -> dict:
    return {
        'system_id': settings.system_id,
        'enabled': settings.enabled,
        'interval_seconds': settings.interval_seconds,
        'retention_hours': settings.retention_hours,
        'revision': settings.revision,
        'is_synced': settings.is_synced,
        'last_synced_at': settings.last_synced_at.isoformat() if settings.last_synced_at else None,
        'last_sync_error': settings.last_sync_error,
        'device_label': device.display_name,
        'screenshot_count': len(device.screenshots) if device.screenshots else 0,
    }


def _coerce_bool(value, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'1', 'true', 'yes', 'on'}:
            return True
        if lowered in {'0', 'false', 'no', 'off'}:
            return False
    raise ValueError(f'{field_name} must be a boolean')


def _coerce_int(value, field_name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field_name} must be an integer') from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f'{field_name} must be between {minimum} and {maximum}')
    return parsed


def upsert_settings(device: AgentDevice, body: dict) -> DeviceRecallSettings:
    settings = get_or_create_settings(device)
    changed = False

    if 'enabled' in body:
        settings.enabled = _coerce_bool(body.get('enabled'), 'enabled')
        changed = True
    if 'interval_seconds' in body:
        settings.interval_seconds = _coerce_int(
            body.get('interval_seconds'),
            'interval_seconds',
            DeviceRecallSettings.MIN_INTERVAL_SECONDS,
            DeviceRecallSettings.MAX_INTERVAL_SECONDS,
        )
        changed = True
    if 'retention_hours' in body:
        settings.retention_hours = _coerce_int(
            body.get('retention_hours'),
            'retention_hours',
            DeviceRecallSettings.MIN_RETENTION_HOURS,
            DeviceRecallSettings.MAX_RETENTION_HOURS,
        )
        changed = True

    if changed:
        payload = build_recall_policy_payload(settings)
        settings.revision = compute_revision(payload)
        settings.is_synced = False
        settings.updated_at = datetime.now(timezone.utc)

    return settings


def _mark_sync_result(settings: DeviceRecallSettings, success: bool, message: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    settings.last_synced_at = now
    if success:
        settings.is_synced = True
        settings.last_sync_error = None
    else:
        settings.is_synced = False
        settings.last_sync_error = (message or 'Recall policy sync failed')[:500]


def sync_recall_policy_for_device(device: AgentDevice) -> tuple[bool, str]:
    from src.agent_helper import AgentClient, AgentConnectionManager

    if not _is_linux_device(device):
        return True, 'Recall policy not applicable for this platform'

    if not AgentConnectionManager.is_online(device.system_id):
        return False, 'Device is offline'

    settings = get_or_create_settings(device)
    payload = build_recall_policy_payload(settings)
    agent = AgentClient(device.system_id)
    success, message = agent.sync_recall_policy(payload)
    _mark_sync_result(settings, success, message)
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOGGER.error('Failed to persist recall sync state for %s: %s', device.system_id, exc)
        return False, 'Database error while saving recall sync state'
    return success, message or ('Recall policy synchronized' if success else 'Recall policy sync failed')


def sync_recall_policies_for_system(system_id: str) -> tuple[bool, str]:
    device = AgentDevice.query.get(system_id)
    if device is None:
        return False, 'Device not found'
    return sync_recall_policy_for_device(device)
