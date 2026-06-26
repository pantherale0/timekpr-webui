import base64
import hashlib
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from src.policy.apparmor import (
    _validate_android_package_name,
    _validate_apparmor_executable_path,
)
from src.models import (
    db,
    AgentDevice,
    ApplicationIcon,
    DeviceInstalledApplication,
    ManagedUserDeviceMap,
)

_LOGGER = logging.getLogger(__name__)

ANDROID_PACKAGE_PREFIX = '/android/package/'
MAX_ICON_BYTES = 32 * 1024
SHA256_HEX_RE = re.compile(r'^[a-f0-9]{64}$')

# In-memory report accumulation keyed by (system_id, report_id)
_pending_reports = defaultdict(dict)


def _normalize_linux_username(value):
    username = (value or '').strip()
    if not username:
        raise ValueError('linux_username is required')
    return username


def _normalize_match_type(platform, match_type):
    normalized = (match_type or '').strip().lower()
    if normalized not in DeviceInstalledApplication.VALID_MATCH_TYPES:
        raise ValueError(f'Unsupported match_type: {match_type}')
    if platform == DeviceInstalledApplication.PLATFORM_ANDROID:
        if normalized != DeviceInstalledApplication.MATCH_TYPE_PACKAGE:
            raise ValueError('Android inventory entries must use match_type package')
    elif normalized != DeviceInstalledApplication.MATCH_TYPE_EXECUTABLE:
        raise ValueError('Linux inventory entries must use match_type executable')
    return normalized


def _normalize_identifier(platform, match_type, identifier):
    raw = (identifier or '').strip()
    if not raw:
        raise ValueError('identifier is required')
    if match_type == DeviceInstalledApplication.MATCH_TYPE_PACKAGE:
        if raw.startswith(ANDROID_PACKAGE_PREFIX):
            package_name = raw[len(ANDROID_PACKAGE_PREFIX):]
        else:
            package_name = raw
        package_name = _validate_android_package_name(package_name)
        return f'{ANDROID_PACKAGE_PREFIX}{package_name}'
    return _validate_apparmor_executable_path(raw)


def normalize_installed_app_entry(platform, entry):
    """Validate and normalize a single inventory entry from an agent report."""
    if not isinstance(entry, dict):
        raise ValueError('App entry must be an object')

    platform_normalized = (platform or '').strip().lower()
    if platform_normalized not in DeviceInstalledApplication.VALID_PLATFORMS:
        raise ValueError(f'Unsupported platform: {platform}')

    application_name = (entry.get('application_name') or '').strip()
    if not application_name:
        raise ValueError('application_name is required')

    match_type = _normalize_match_type(platform_normalized, entry.get('match_type'))
    identifier = _normalize_identifier(platform_normalized, match_type, entry.get('identifier'))

    version_name = entry.get('version_name')
    if version_name is not None:
        version_name = str(version_name).strip() or None

    icon_hash = entry.get('icon_hash')
    if icon_hash is not None:
        icon_hash = str(icon_hash).strip().lower() or None
        if icon_hash and not SHA256_HEX_RE.match(icon_hash):
            raise ValueError('icon_hash must be a 64-character lowercase hex SHA-256 digest')

    return {
        'application_name': application_name[:120],
        'identifier': identifier[:512],
        'match_type': match_type,
        'platform': platform_normalized,
        'version_name': version_name[:120] if version_name else None,
        'icon_hash': icon_hash,
    }


def _device_platform(system_id):
    device = AgentDevice.query.get(system_id)
    if device is None:
        raise ValueError(f'Unknown device: {system_id}')
    platform = (device.platform or DeviceInstalledApplication.PLATFORM_LINUX).strip().lower()
    if platform not in DeviceInstalledApplication.VALID_PLATFORMS:
        platform = DeviceInstalledApplication.PLATFORM_LINUX
    return device, platform


def begin_report(system_id, report_id, linux_username):
    report_key = (system_id, report_id)
    linux_username = _normalize_linux_username(linux_username)
    _device_platform(system_id)
    _pending_reports[report_key] = {
        'linux_username': linux_username,
        'apps': [],
    }
    return report_key


def ingest_chunk(system_id, report_id, linux_username, apps):
    report_key = (system_id, report_id)
    pending = _pending_reports.get(report_key)
    if pending is None:
        begin_report(system_id, report_id, linux_username)
        pending = _pending_reports[report_key]

    expected_username = pending['linux_username']
    actual_username = _normalize_linux_username(linux_username)
    if actual_username != expected_username:
        raise ValueError('linux_username mismatch within report session')

    _, platform = _device_platform(system_id)
    if not isinstance(apps, list):
        raise ValueError('apps must be a list')

    normalized_apps = []
    for entry in apps:
        normalized_apps.append(normalize_installed_app_entry(platform, entry))
    pending['apps'].extend(normalized_apps)
    return len(normalized_apps)


def _compute_report_hash(apps):
    lines = []
    for app in sorted(apps, key=lambda item: (item['identifier'], item['match_type'])):
        lines.append(
            '|'.join([
                app['identifier'],
                app['match_type'],
                app['application_name'],
                app.get('version_name') or '',
                app.get('icon_hash') or '',
            ])
        )
    digest = hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()
    return digest


def finalize_report(system_id, report_id, reported_at=None):
    report_key = (system_id, report_id)
    pending = _pending_reports.pop(report_key, None)
    if pending is None:
        raise ValueError(f'Unknown report session: {report_id}')

    device, _platform = _device_platform(system_id)
    linux_username = pending['linux_username']
    apps = pending['apps']
    now = reported_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    seen_keys = set()
    upserted = 0
    for app in apps:
        key = (app['identifier'], app['match_type'])
        if key in seen_keys:
            continue
        seen_keys.add(key)

        row = DeviceInstalledApplication.query.filter_by(
            system_id=system_id,
            linux_username=linux_username,
            identifier=app['identifier'],
            match_type=app['match_type'],
        ).first()

        if row is None:
            row = DeviceInstalledApplication(
                system_id=system_id,
                linux_username=linux_username,
                application_name=app['application_name'],
                identifier=app['identifier'],
                match_type=app['match_type'],
                platform=app['platform'],
                version_name=app['version_name'],
                icon_hash=app['icon_hash'],
                first_seen_at=now,
                last_seen_at=now,
                is_present=True,
            )
            db.session.add(row)
            upserted += 1
        else:
            row.application_name = app['application_name']
            row.platform = app['platform']
            row.version_name = app['version_name']
            row.icon_hash = app['icon_hash']
            row.last_seen_at = now
            row.is_present = True
            upserted += 1

    removed = 0
    existing_rows = DeviceInstalledApplication.query.filter_by(
        system_id=system_id,
        linux_username=linux_username,
        is_present=True,
    ).all()
    for row in existing_rows:
        key = (row.identifier, row.match_type)
        if key not in seen_keys:
            row.is_present = False
            row.last_seen_at = now
            removed += 1

    report_hash = _compute_report_hash(apps)
    device.installed_apps_report_hash = report_hash
    device.installed_apps_last_reported = now
    device.installed_apps_count = len(seen_keys)

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise

    return {
        'success': True,
        'apps_upserted': upserted,
        'apps_removed': removed,
        'apps_total': len(seen_keys),
        'report_hash': report_hash,
    }


def abort_report(system_id, report_id):
    _pending_reports.pop((system_id, report_id), None)


def store_icon(content_hash, mime_type, png_bytes):
    normalized_hash = (content_hash or '').strip().lower()
    if not SHA256_HEX_RE.match(normalized_hash):
        raise ValueError('content_hash must be a 64-character lowercase hex SHA-256 digest')
    if not png_bytes:
        raise ValueError('Icon data is required')
    if len(png_bytes) > MAX_ICON_BYTES:
        raise ValueError(f'Icon exceeds maximum size of {MAX_ICON_BYTES} bytes')

    actual_hash = hashlib.sha256(png_bytes).hexdigest()
    if actual_hash != normalized_hash:
        raise ValueError('content_hash does not match icon data')

    normalized_mime = (mime_type or 'image/png').strip().lower()
    if normalized_mime != 'image/png':
        raise ValueError('Only image/png icons are supported')

    existing = ApplicationIcon.query.get(normalized_hash)
    if existing is not None:
        return existing

    icon = ApplicationIcon(
        content_hash=normalized_hash,
        mime_type=normalized_mime,
        data=png_bytes,
    )
    db.session.add(icon)
    db.session.commit()
    return icon


def store_icon_from_base64(content_hash, mime_type, data_base64):
    if not data_base64:
        raise ValueError('data_base64 is required')
    try:
        png_bytes = base64.b64decode(data_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid base64 icon data') from exc
    return store_icon(content_hash, mime_type, png_bytes)


def get_icon(content_hash):
    normalized_hash = (content_hash or '').strip().lower()
    if not SHA256_HEX_RE.match(normalized_hash):
        return None
    return ApplicationIcon.query.get(normalized_hash)


def list_installed_apps_for_device(system_id, linux_username=None, present_only=True):
    query = DeviceInstalledApplication.query.filter_by(system_id=system_id)
    if linux_username is not None:
        query = query.filter_by(linux_username=_normalize_linux_username(linux_username))
    if present_only:
        query = query.filter_by(is_present=True)
    return query.order_by(
        DeviceInstalledApplication.application_name.asc(),
        DeviceInstalledApplication.identifier.asc(),
    ).all()


def list_installed_apps_for_managed_user(managed_user_id, present_only=True):
    mappings = ManagedUserDeviceMap.query.filter_by(managed_user_id=managed_user_id).all()
    if not mappings:
        return []

    results = []
    seen = set()
    for mapping in mappings:
        apps = list_installed_apps_for_device(
            mapping.system_id,
            linux_username=mapping.linux_username,
            present_only=present_only,
        )
        for app in apps:
            dedupe_key = (app.system_id, app.linux_username, app.identifier, app.match_type)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            payload = app.to_dict()
            payload['device_hostname'] = mapping.device.system_hostname if mapping.device else None
            results.append(payload)

    results.sort(key=lambda item: (item['application_name'].lower(), item['identifier']))
    return results


def list_discovered_apps_for_platform(platform, present_only=True):
    """Return installed apps from all approved devices matching the given platform."""
    from src.models import AppPolicy

    normalized_platform = (platform or AppPolicy.PLATFORM_LINUX).strip().lower()
    if normalized_platform not in AppPolicy.VALID_PLATFORMS:
        normalized_platform = AppPolicy.PLATFORM_LINUX

    query = (
        DeviceInstalledApplication.query
        .join(AgentDevice, DeviceInstalledApplication.system_id == AgentDevice.system_id)
        .filter(AgentDevice.status == 'approved')
    )
    if present_only:
        query = query.filter(DeviceInstalledApplication.is_present.is_(True))

    if normalized_platform == AppPolicy.PLATFORM_ANDROID:
        query = query.filter(AgentDevice.platform == AppPolicy.PLATFORM_ANDROID)
    else:
        query = query.filter(
            db.or_(
                AgentDevice.platform == AppPolicy.PLATFORM_LINUX,
                AgentDevice.platform.is_(None),
                AgentDevice.platform == '',
            )
        )

    results = []
    seen = set()
    for app in query.order_by(
        DeviceInstalledApplication.application_name.asc(),
        DeviceInstalledApplication.identifier.asc(),
    ).all():
        dedupe_key = (app.identifier, app.match_type)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        payload = app.to_dict()
        payload['device_hostname'] = app.device.system_hostname if app.device else None
        results.append(payload)

    return results


def list_installed_apps_for_policy(policy_id, present_only=True):
    """Return discovered apps for a policy based on its platform."""
    from src.models import AppPolicy

    policy = AppPolicy.query.get(policy_id)
    if policy is None:
        return []
    return list_discovered_apps_for_platform(policy.platform, present_only=present_only)


def installed_app_to_policy_fields(app):
    if isinstance(app, DeviceInstalledApplication):
        return app.to_policy_fields()
    if isinstance(app, dict):
        return {
            'application_name': app['application_name'],
            'executable_path': app['identifier'],
            'match_type': app['match_type'],
        }
    raise TypeError('Unsupported app record type')


def handle_installed_apps_report(system_id, message):
    report_id = (message.get('report_id') or '').strip()
    if not report_id:
        raise ValueError('report_id is required')

    linux_username = message.get('linux_username')
    apps = message.get('apps') or []
    is_final = bool(message.get('is_final'))

    reported_at_raw = message.get('reported_at')
    reported_at = None
    if reported_at_raw:
        reported_at = datetime.fromisoformat(str(reported_at_raw).replace('Z', '+00:00'))

    ingest_chunk(system_id, report_id, linux_username, apps)

    if is_final:
        return finalize_report(system_id, report_id, reported_at=reported_at)

    return {
        'success': True,
        'apps_upserted': 0,
        'apps_removed': 0,
        'pending': True,
    }


def handle_app_icon_report(message):
    content_hash = message.get('content_hash')
    mime_type = message.get('mime_type')
    data_base64 = message.get('data_base64')
    icon = store_icon_from_base64(content_hash, mime_type, data_base64)
    return {
        'success': True,
        'content_hash': icon.content_hash,
        'created': True,
    }
