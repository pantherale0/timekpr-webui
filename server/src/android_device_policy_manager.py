"""Business logic for AMAPI-aligned Android device restriction policies."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from src.database import (
    AgentDevice,
    AppPolicy,
    db,
    ManagedUserDeviceMap,
    MappingAndroidDevicePolicy,
    AndroidForceInstalledApp,
)

_LOGGER = logging.getLogger(__name__)


def _is_android_device(device: AgentDevice) -> bool:
    platform = (device.platform if device else None) or AppPolicy.PLATFORM_LINUX
    return platform == AppPolicy.PLATFORM_ANDROID


def _require_android_device(device: AgentDevice) -> None:
    if not _is_android_device(device):
        raise ValueError('Android device policy is only supported for Android devices')


def _normalize_camera_access(value: str | None) -> str:
    normalized = (value or MappingAndroidDevicePolicy.CAMERA_ACCESS_UNSPECIFIED).strip().upper()
    if normalized not in MappingAndroidDevicePolicy.VALID_CAMERA_ACCESS:
        raise ValueError(f'Unsupported camera_access value: {value}')
    return normalized


def _normalize_developer_settings(value: str | None) -> str:
    normalized = (value or MappingAndroidDevicePolicy.DEVELOPER_SETTINGS_UNSPECIFIED).strip().upper()
    if normalized not in MappingAndroidDevicePolicy.VALID_DEVELOPER_SETTINGS:
        raise ValueError(f'Unsupported developer_settings value: {value}')
    return normalized


def _normalize_microphone_access(value: str | None) -> str:
    normalized = (value or MappingAndroidDevicePolicy.MICROPHONE_ACCESS_UNSPECIFIED).strip().upper()
    if normalized not in MappingAndroidDevicePolicy.VALID_MICROPHONE_ACCESS:
        raise ValueError(f'Unsupported microphone_access value: {value}')
    return normalized


def _normalize_usb_data_access(value: str | None) -> str:
    normalized = (value or MappingAndroidDevicePolicy.USB_DATA_ACCESS_UNSPECIFIED).strip().upper()
    if normalized not in MappingAndroidDevicePolicy.VALID_USB_DATA_ACCESS:
        raise ValueError(f'Unsupported usb_data_access value: {value}')
    return normalized


def _normalize_support_message(value, field_name: str, max_length: int) -> str:
    normalized = (value or '').strip()
    if not normalized:
        raise ValueError(f'{field_name} must not be empty')
    if len(normalized) > max_length:
        raise ValueError(f'{field_name} exceeds maximum length of {max_length}')
    return normalized


def _user_facing_message(default_message: str) -> dict:
    return {'defaultMessage': default_message}


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


def _owner_lockdown_flags(system_id: str) -> tuple[bool, list[int]]:
    """Return (lock_owner_profile, managed_child_uids) for Android multi-user devices."""
    mappings = ManagedUserDeviceMap.query.filter_by(system_id=system_id).all()
    owner_mapped_to_child = any(m.linux_uid == 0 for m in mappings)
    managed_child_uids = sorted(
        {
            int(m.linux_uid)
            for m in mappings
            if m.linux_uid is not None and int(m.linux_uid) > 0
        }
    )
    has_managed_children = bool(managed_child_uids) or any(
        m.android_profile_type in ('restricted', 'standard')
        for m in mappings
    )
    lock_owner_profile = has_managed_children and not owner_mapped_to_child
    return lock_owner_profile, managed_child_uids


def build_device_policy_payload(policy: MappingAndroidDevicePolicy) -> dict:
    """Build canonical AMAPI-shaped device policy JSON for agent sync."""
    short_message = (
        policy.short_support_message
        or MappingAndroidDevicePolicy.DEFAULT_SHORT_SUPPORT_MESSAGE
    )
    long_message = (
        policy.long_support_message
        or MappingAndroidDevicePolicy.DEFAULT_LONG_SUPPORT_MESSAGE
    )
    
    lock_owner_profile, managed_child_uids = _owner_lockdown_flags(policy.system_id)

    # Query profiles to provision (skip once linked to a device UID)
    mappings = ManagedUserDeviceMap.query.filter_by(system_id=policy.system_id).all()
    profiles = []
    for m in mappings:
        if m.android_profile_type not in ('restricted', 'standard'):
            continue
        if m.linux_uid is not None:
            continue
        profiles.append({
            'username': m.linux_username,
            'profile_type': m.android_profile_type,
        })

    force_installed_apps = []
    if policy.force_installed_apps:
        for app in policy.force_installed_apps:
            force_installed_apps.append({
                'packageName': app.package_name,
                'apkUrl': app.apk_url,
                'sha256Checksum': app.sha256_checksum,
            })

    payload = {
        'screenCaptureDisabled': bool(policy.screen_capture_disabled),
        'cameraAccess': policy.camera_access,
        'microphoneAccess': policy.microphone_access,
        'installAppsDisabled': bool(policy.install_apps_disabled),
        'uninstallAppsDisabled': bool(policy.uninstall_apps_disabled),
        'factoryResetDisabled': bool(policy.factory_reset_disabled),
        'adjustVolumeDisabled': bool(policy.adjust_volume_disabled),
        'modifyAccountsDisabled': bool(policy.modify_accounts_disabled),
        'mountPhysicalMediaDisabled': bool(policy.mount_physical_media_disabled),
        'bluetoothDisabled': bool(policy.bluetooth_disabled),
        'outgoingCallsDisabled': bool(policy.outgoing_calls_disabled),
        'smsDisabled': bool(policy.sms_disabled),
        'blockWifiTethering': bool(policy.block_wifi_tethering),
        'blockNfc': bool(policy.block_nfc),
        'advancedSecurityOverrides': {
            'developerSettings': policy.developer_settings,
        },
        'deviceConnectivityManagement': {
            'usbDataAccess': policy.usb_data_access,
        },
        'shortSupportMessage': _user_facing_message(short_message),
        'longSupportMessage': _user_facing_message(long_message),
        'profiles': profiles,
        'lockOwnerProfile': lock_owner_profile,
        'managedProfileUids': managed_child_uids,
        'forceInstalledApps': force_installed_apps,
    }
    return payload


def compute_revision(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _get_policy_row(device: AgentDevice) -> MappingAndroidDevicePolicy | None:
    return MappingAndroidDevicePolicy.query.filter_by(system_id=device.system_id).first()


def get_or_create_policy(device: AgentDevice) -> MappingAndroidDevicePolicy:
    _require_android_device(device)
    policy = _get_policy_row(device)
    if policy is None:
        payload = build_device_policy_payload(
            MappingAndroidDevicePolicy(system_id=device.system_id),
        )
        policy = MappingAndroidDevicePolicy(
            system_id=device.system_id,
            revision=compute_revision(payload),
        )
        db.session.add(policy)
        db.session.flush()
    return policy


def build_policy_summary(policy: MappingAndroidDevicePolicy, device: AgentDevice) -> dict:
    return {
        'system_id': device.system_id,
        'device_label': (device.system_hostname if device else None) or device.system_id,
        'platform': (device.platform if device else None) or AppPolicy.PLATFORM_ANDROID,
        'screen_capture_disabled': policy.screen_capture_disabled,
        'camera_access': policy.camera_access,
        'microphone_access': policy.microphone_access,
        'install_apps_disabled': policy.install_apps_disabled,
        'uninstall_apps_disabled': policy.uninstall_apps_disabled,
        'factory_reset_disabled': policy.factory_reset_disabled,
        'adjust_volume_disabled': policy.adjust_volume_disabled,
        'modify_accounts_disabled': policy.modify_accounts_disabled,
        'mount_physical_media_disabled': policy.mount_physical_media_disabled,
        'bluetooth_disabled': policy.bluetooth_disabled,
        'outgoing_calls_disabled': policy.outgoing_calls_disabled,
        'sms_disabled': policy.sms_disabled,
        'usb_data_access': policy.usb_data_access,
        'developer_settings': policy.developer_settings,
        'block_wifi_tethering': policy.block_wifi_tethering,
        'block_nfc': policy.block_nfc,
        'short_support_message': (
            policy.short_support_message
            or MappingAndroidDevicePolicy.DEFAULT_SHORT_SUPPORT_MESSAGE
        ),
        'long_support_message': (
            policy.long_support_message
            or MappingAndroidDevicePolicy.DEFAULT_LONG_SUPPORT_MESSAGE
        ),
        'revision': policy.revision,
        'is_synced': policy.is_synced,
        'last_synced_at': policy.last_synced_at.isoformat() if policy.last_synced_at else None,
        'last_sync_error': policy.last_sync_error,
        'device_policy': build_device_policy_payload(policy),
        'force_installed_apps': [
            {
                'package_name': app.package_name,
                'apk_url': app.apk_url,
                'sha256_checksum': app.sha256_checksum or '',
            }
            for app in policy.force_installed_apps
        ],
    }


def upsert_policy(device: AgentDevice, body: dict) -> MappingAndroidDevicePolicy:
    _require_android_device(device)
    if not isinstance(body, dict):
        raise ValueError('Request body must be a JSON object')

    policy = get_or_create_policy(device)

    if 'screen_capture_disabled' in body:
        policy.screen_capture_disabled = _coerce_bool(
            body.get('screen_capture_disabled'),
            'screen_capture_disabled',
        )
    if 'camera_access' in body:
        policy.camera_access = _normalize_camera_access(body.get('camera_access'))
    if 'microphone_access' in body:
        policy.microphone_access = _normalize_microphone_access(body.get('microphone_access'))
    if 'usb_data_access' in body:
        policy.usb_data_access = _normalize_usb_data_access(body.get('usb_data_access'))
    if 'install_apps_disabled' in body:
        policy.install_apps_disabled = _coerce_bool(
            body.get('install_apps_disabled'),
            'install_apps_disabled',
        )
    if 'uninstall_apps_disabled' in body:
        policy.uninstall_apps_disabled = _coerce_bool(
            body.get('uninstall_apps_disabled'),
            'uninstall_apps_disabled',
        )
    if 'developer_settings' in body:
        policy.developer_settings = _normalize_developer_settings(body.get('developer_settings'))
    for bool_field in (
        'factory_reset_disabled',
        'adjust_volume_disabled',
        'modify_accounts_disabled',
        'mount_physical_media_disabled',
        'bluetooth_disabled',
        'outgoing_calls_disabled',
        'sms_disabled',
    ):
        if bool_field in body:
            setattr(
                policy,
                bool_field,
                _coerce_bool(body.get(bool_field), bool_field),
            )
    if 'short_support_message' in body:
        policy.short_support_message = _normalize_support_message(
            body.get('short_support_message'),
            'short_support_message',
            MappingAndroidDevicePolicy.MAX_SHORT_SUPPORT_MESSAGE_LENGTH,
        )
    if 'long_support_message' in body:
        policy.long_support_message = _normalize_support_message(
            body.get('long_support_message'),
            'long_support_message',
            MappingAndroidDevicePolicy.MAX_LONG_SUPPORT_MESSAGE_LENGTH,
        )
    if 'block_wifi_tethering' in body:
        policy.block_wifi_tethering = _coerce_bool(
            body.get('block_wifi_tethering'),
            'block_wifi_tethering',
        )
    if 'block_nfc' in body:
        policy.block_nfc = _coerce_bool(
            body.get('block_nfc'),
            'block_nfc',
        )

    if 'force_installed_apps' in body:
        app_list = body.get('force_installed_apps')
        if not isinstance(app_list, list):
            raise ValueError('force_installed_apps must be a list')

        # Clean existing entries
        AndroidForceInstalledApp.query.filter_by(system_id=policy.system_id).delete()

        # Insert new entries
        for app_data in app_list:
            if not isinstance(app_data, dict):
                raise ValueError('Each item in force_installed_apps must be an object')
            package_name = (app_data.get('package_name') or '').strip()
            apk_url = (app_data.get('apk_url') or '').strip()
            sha256_checksum = (app_data.get('sha256_checksum') or '').strip() or None

            if not package_name:
                raise ValueError('package_name is required for each force-installed app')
            if not apk_url:
                raise ValueError('apk_url is required for each force-installed app')

            new_app = AndroidForceInstalledApp(
                system_id=policy.system_id,
                package_name=package_name,
                apk_url=apk_url,
                sha256_checksum=sha256_checksum
            )
            db.session.add(new_app)

    payload = build_device_policy_payload(policy)
    policy.revision = compute_revision(payload)
    policy.is_synced = False
    policy.last_sync_error = None
    policy.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    push_success, push_message = push_device_policy(device)
    if push_success and push_message and 'Queued' not in push_message:
        policy.is_synced = True
        policy.last_synced_at = datetime.now(timezone.utc)
        policy.last_sync_error = None
    else:
        policy.is_synced = False
        policy.last_sync_error = push_message
    db.session.commit()
    return policy


def push_device_policy(device: AgentDevice) -> tuple[bool, str]:
    """Push device restriction policy to the agent when online."""
    from src.agent_helper import AgentClient, AgentConnectionManager

    if not _is_android_device(device):
        return False, 'Not an Android device'

    policy = _get_policy_row(device)
    if policy is None:
        policy = get_or_create_policy(device)
        db.session.commit()

    if not AgentConnectionManager.is_online(device.system_id):
        from src.pending_commands_manager import enqueue_policy_snapshot

        try:
            enqueue_policy_snapshot(device.system_id, 'sync_android_device_policy', 'system')
            return True, 'Queued for reconnect'
        except ValueError as exc:
            return False, str(exc)

    payload = build_device_policy_payload(policy)
    agent = AgentClient(system_id=device.system_id)
    return agent.sync_android_device_policy('system', payload)


def push_mapping_device_policy(mapping: ManagedUserDeviceMap) -> tuple[bool, str]:
    """Backward compatibility wrapper."""
    if not mapping or not mapping.device:
        return False, 'Invalid mapping'
    return push_device_policy(mapping.device)


def sync_android_device_policies_for_system(system_id: str) -> tuple[bool, str]:
    """Push device policies for Android device."""
    device = AgentDevice.query.get(system_id)
    if device is None or (device.platform or '') != AppPolicy.PLATFORM_ANDROID:
        return True, 'No Android device policy sync required'

    try:
        success, message = push_device_policy(device)
    except (OSError, RuntimeError, SQLAlchemyError, ValueError) as exc:
        success = False
        message = str(exc)

    policy = _get_policy_row(device)
    if policy is not None:
        if success:
            policy.is_synced = True
            policy.last_synced_at = datetime.now(timezone.utc)
            policy.last_sync_error = None
        else:
            policy.is_synced = False
            policy.last_sync_error = message
        db.session.commit()

    return success, message
