"""Storage and retrieval for agent desktop screenshot history."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc
from sqlalchemy.exc import SQLAlchemyError

from src.database import AgentDevice, DeviceScreenshotSettings, DeviceScreenshot, db

_LOGGER = logging.getLogger(__name__)

MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024
ALLOWED_MIME_TYPES = {'image/jpeg', 'image/png'}
SHA256_HEX_RE = re.compile(r'^[a-f0-9]{64}$')
SCREENSHOT_ID_RE = re.compile(r'^[a-f0-9-]{36}$', re.IGNORECASE)


def _normalize_linux_username(value):
    if value is None:
        return None
    username = str(value).strip()
    if not username:
        return None
    if len(username) > 80:
        raise ValueError('linux_username exceeds maximum length of 80')
    return username


def parse_screenshot_timestamp(value):
    if not isinstance(value, str):
        raise ValueError('captured_at must be an ISO-8601 string')

    normalized = value.strip()
    if not normalized:
        raise ValueError('captured_at must not be empty')

    if normalized.endswith('Z'):
        normalized = normalized[:-1] + '+00:00'

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError('captured_at must be a valid ISO-8601 timestamp') from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def _decode_image_data(data_base64: str) -> bytes:
    if not isinstance(data_base64, str) or not data_base64.strip():
        raise ValueError('data_base64 is required')
    try:
        decoded = base64.b64decode(data_base64, validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError('data_base64 must be valid base64') from exc
    if not decoded:
        raise ValueError('Screenshot payload is empty')
    if len(decoded) > MAX_SCREENSHOT_BYTES:
        raise ValueError(f'Screenshot exceeds maximum size of {MAX_SCREENSHOT_BYTES} bytes')
    return decoded


def normalize_screenshot_report(system_id: str, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('screenshot report must be an object')

    screenshot_id = (payload.get('screenshot_id') or '').strip()
    if not screenshot_id or len(screenshot_id) > 64:
        raise ValueError('screenshot_id is required')
    if not SCREENSHOT_ID_RE.match(screenshot_id):
        raise ValueError('screenshot_id must be a UUID string')

    mime_type = (payload.get('mime_type') or 'image/jpeg').strip().lower()
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValueError('mime_type must be image/jpeg or image/png')

    captured_at = parse_screenshot_timestamp(payload.get('captured_at'))
    linux_username = _normalize_linux_username(payload.get('linux_username'))
    image_bytes = _decode_image_data(payload.get('data_base64'))

    content_hash = hashlib.sha256(image_bytes).hexdigest()
    reported_hash = (payload.get('content_hash') or '').strip().lower()
    if reported_hash and reported_hash != content_hash:
        raise ValueError('content_hash does not match screenshot data')

    width = payload.get('width')
    height = payload.get('height')
    if width is not None:
        width = int(width)
        if width <= 0 or width > 10000:
            raise ValueError('width is out of range')
    if height is not None:
        height = int(height)
        if height <= 0 or height > 10000:
            raise ValueError('height is out of range')

    active_window_title = payload.get('active_window_title')
    if active_window_title is not None:
        active_window_title = str(active_window_title).strip()[:255] or None

    return {
        'system_id': system_id,
        'screenshot_id': screenshot_id,
        'linux_username': linux_username,
        'captured_at': captured_at,
        'mime_type': mime_type,
        'width': width,
        'height': height,
        'content_hash': content_hash,
        'active_window_title': active_window_title,
        'data': image_bytes,
    }


def prune_expired_screenshots(system_id: str, retention_hours: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(retention_hours, 1))
    deleted = (
        DeviceScreenshot.query.filter(
            DeviceScreenshot.system_id == system_id,
            DeviceScreenshot.captured_at < cutoff,
        )
        .delete(synchronize_session=False)
    )
    return deleted


def handle_screenshot_report(system_id: str, payload: dict) -> dict:
    device = AgentDevice.query.get(system_id)
    if device is None:
        raise ValueError('Unknown device')

    normalized = normalize_screenshot_report(system_id, payload)
    existing = DeviceScreenshot.query.filter_by(
        system_id=system_id,
        screenshot_id=normalized['screenshot_id'],
    ).first()
    if existing is not None:
        return {
            'success': True,
            'duplicate': True,
            'screenshot_id': existing.screenshot_id,
        }

    screenshot = DeviceScreenshot(**normalized)
    db.session.add(screenshot)

    retention_hours = DeviceScreenshotSettings.DEFAULT_RETENTION_HOURS
    if device.screenshot_settings is not None:
        retention_hours = device.screenshot_settings.retention_hours
    prune_expired_screenshots(system_id, retention_hours)

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOGGER.error('Failed to store screenshot for %s: %s', system_id, exc)
        raise

    return {
        'success': True,
        'duplicate': False,
        'screenshot_id': screenshot.screenshot_id,
        'id': screenshot.id,
    }


def list_screenshots_for_device(
    system_id: str,
    *,
    page: int = 1,
    per_page: int = 24,
    linux_username: str | None = None,
) -> dict:
    query = DeviceScreenshot.query.filter_by(system_id=system_id)
    if linux_username:
        query = query.filter(DeviceScreenshot.linux_username == linux_username)

    total = query.count()
    page = max(page, 1)
    per_page = min(max(per_page, 1), 100)
    items = (
        query.order_by(desc(DeviceScreenshot.captured_at))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return {
        'items': [item.to_summary_dict() for item in items],
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page if total else 0,
    }


def get_screenshot_by_id(screenshot_db_id: int) -> DeviceScreenshot | None:
    return DeviceScreenshot.query.get(screenshot_db_id)


def delete_screenshot(screenshot: DeviceScreenshot) -> None:
    db.session.delete(screenshot)
    db.session.commit()


def delete_all_screenshots_for_device(system_id: str) -> int:
    deleted = DeviceScreenshot.query.filter_by(system_id=system_id).delete(
        synchronize_session=False,
    )
    db.session.commit()
    return deleted
