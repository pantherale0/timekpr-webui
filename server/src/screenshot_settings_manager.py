"""Business logic for device screenshot capture settings and agent sync."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from src.database import AgentDevice, DeviceScreenshotSettings, db

_LOGGER = logging.getLogger(__name__)


def _is_desktop_device(device: AgentDevice) -> bool:
    platform = (device.platform or 'linux').strip().lower()
    return platform not in {'android', 'nintendo', 'xbox'}


def build_screenshot_policy_payload(settings: DeviceScreenshotSettings) -> dict:
    return {
        'enabled': bool(settings.enabled),
        'intervalSeconds': int(settings.interval_seconds),
    }


def compute_revision(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def get_or_create_settings(device: AgentDevice) -> DeviceScreenshotSettings:
    if not _is_desktop_device(device):
        raise ValueError('Screen history is only supported for Linux and Windows devices')

    settings = device.screenshot_settings
    if settings is None:
        settings = DeviceScreenshotSettings(
            system_id=device.system_id,
            enabled=False,
            interval_seconds=DeviceScreenshotSettings.DEFAULT_INTERVAL_SECONDS,
            retention_hours=DeviceScreenshotSettings.DEFAULT_RETENTION_HOURS,
        )
        payload = build_screenshot_policy_payload(settings)
        settings.revision = compute_revision(payload)
        db.session.add(settings)
        db.session.flush()
    return settings


def build_settings_summary(settings: DeviceScreenshotSettings, device: AgentDevice) -> dict:
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


def upsert_settings(device: AgentDevice, body: dict) -> DeviceScreenshotSettings:
    settings = get_or_create_settings(device)
    changed = False

    if 'enabled' in body:
        settings.enabled = _coerce_bool(body.get('enabled'), 'enabled')
        changed = True
    if 'interval_seconds' in body:
        settings.interval_seconds = _coerce_int(
            body.get('interval_seconds'),
            'interval_seconds',
            DeviceScreenshotSettings.MIN_INTERVAL_SECONDS,
            DeviceScreenshotSettings.MAX_INTERVAL_SECONDS,
        )
        changed = True
    if 'retention_hours' in body:
        settings.retention_hours = _coerce_int(
            body.get('retention_hours'),
            'retention_hours',
            DeviceScreenshotSettings.MIN_RETENTION_HOURS,
            DeviceScreenshotSettings.MAX_RETENTION_HOURS,
        )
        changed = True

    if changed:
        payload = build_screenshot_policy_payload(settings)
        settings.revision = compute_revision(payload)
        settings.is_synced = False
        settings.updated_at = datetime.now(timezone.utc)

    return settings


def _mark_sync_result(settings: DeviceScreenshotSettings, success: bool, message: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    settings.last_synced_at = now
    if success:
        settings.is_synced = True
        settings.last_sync_error = None
    else:
        settings.is_synced = False
        settings.last_sync_error = (message or 'Screenshot policy sync failed')[:500]


def sync_screenshot_policy_for_device(device: AgentDevice) -> tuple[bool, str]:
    from src.agent_helper import AgentClient, AgentConnectionManager

    if not _is_desktop_device(device):
        return True, 'Screenshot policy not applicable for this platform'

    if not AgentConnectionManager.is_online(device.system_id):
        from src.pending_commands_manager import enqueue_policy_snapshot

        try:
            enqueue_policy_snapshot(device.system_id, 'sync_screenshot_policy', '')
            return True, 'Queued for reconnect'
        except ValueError as exc:
            return False, str(exc)

    settings = get_or_create_settings(device)
    payload = build_screenshot_policy_payload(settings)
    agent = AgentClient(device.system_id)
    success, message = agent.sync_screenshot_policy(payload)
    _mark_sync_result(settings, success, message)
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOGGER.error('Failed to persist screenshot sync state for %s: %s', device.system_id, exc)
        return False, 'Database error while saving screenshot sync state'
    return success, message or ('Screenshot policy synchronized' if success else 'Screenshot policy sync failed')


def sync_screenshot_policies_for_system(system_id: str) -> tuple[bool, str]:
    device = AgentDevice.query.get(system_id)
    if device is None:
        return False, 'Device not found'
    return sync_screenshot_policy_for_device(device)
