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
)

_LOGGER = logging.getLogger(__name__)


def _is_android_mapping(mapping: ManagedUserDeviceMap) -> bool:
    platform = (mapping.device.platform if mapping.device else None) or AppPolicy.PLATFORM_LINUX
    return platform == AppPolicy.PLATFORM_ANDROID


def _require_android_mapping(mapping: ManagedUserDeviceMap) -> None:
    if not _is_android_mapping(mapping):
        raise ValueError('Android device policy is only supported for Android device mappings')


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
    }
    return payload


def compute_revision(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _get_policy_row(mapping: ManagedUserDeviceMap) -> MappingAndroidDevicePolicy | None:
    return MappingAndroidDevicePolicy.query.filter_by(device_map_id=mapping.id).first()


def get_or_create_policy(mapping: ManagedUserDeviceMap) -> MappingAndroidDevicePolicy:
    _require_android_mapping(mapping)
    policy = _get_policy_row(mapping)
    if policy is None:
        payload = build_device_policy_payload(
            MappingAndroidDevicePolicy(device_map_id=mapping.id),
        )
        policy = MappingAndroidDevicePolicy(
            device_map_id=mapping.id,
            revision=compute_revision(payload),
        )
        db.session.add(policy)
        db.session.flush()
    return policy


def build_policy_summary(policy: MappingAndroidDevicePolicy, mapping: ManagedUserDeviceMap) -> dict:
    device = mapping.device
    return {
        'device_map_id': mapping.id,
        'system_id': mapping.system_id,
        'linux_username': mapping.linux_username,
        'device_label': (device.system_hostname if device else None) or mapping.system_id,
        'platform': (device.platform if device else None) or AppPolicy.PLATFORM_LINUX,
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
    }


def upsert_policy(mapping: ManagedUserDeviceMap, body: dict) -> MappingAndroidDevicePolicy:
    _require_android_mapping(mapping)
    if not isinstance(body, dict):
        raise ValueError('Request body must be a JSON object')

    policy = get_or_create_policy(mapping)

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

    payload = build_device_policy_payload(policy)
    policy.revision = compute_revision(payload)
    policy.is_synced = False
    policy.last_sync_error = None
    policy.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    push_success, push_message = push_mapping_device_policy(mapping)
    if push_success:
        policy.is_synced = True
        policy.last_synced_at = datetime.now(timezone.utc)
        policy.last_sync_error = None
    else:
        policy.is_synced = False
        policy.last_sync_error = push_message
    db.session.commit()
    return policy


def push_mapping_device_policy(mapping: ManagedUserDeviceMap) -> tuple[bool, str]:
    """Push device restriction policy to the agent when online."""
    from src.agent_helper import AgentClient, AgentConnectionManager

    if not _is_android_mapping(mapping):
        return False, 'Not an Android mapping'

    policy = _get_policy_row(mapping)
    if policy is None:
        policy = get_or_create_policy(mapping)
        db.session.commit()

    if not AgentConnectionManager.is_online(mapping.system_id):
        return False, 'Agent offline'

    payload = build_device_policy_payload(policy)
    agent = AgentClient(system_id=mapping.system_id)
    return agent.sync_android_device_policy(mapping.linux_username, payload)


def sync_android_device_policies_for_system(system_id: str) -> tuple[bool, str]:
    """Push device policies for all Android mappings on a connected device."""
    device = AgentDevice.query.get(system_id)
    if device is None or (device.platform or '') != AppPolicy.PLATFORM_ANDROID:
        return True, 'No Android device policy sync required'

    mappings = ManagedUserDeviceMap.query.filter_by(system_id=system_id).all()
    if not mappings:
        return True, 'No mappings for device'

    pushed = 0
    errors = []
    for mapping in mappings:
        if not _is_android_mapping(mapping):
            continue
        try:
            success, message = push_mapping_device_policy(mapping)
        except (OSError, RuntimeError, SQLAlchemyError, ValueError) as exc:
            success = False
            message = str(exc)
        policy = _get_policy_row(mapping)
        if policy is not None:
            if success:
                policy.is_synced = True
                policy.last_synced_at = datetime.now(timezone.utc)
                policy.last_sync_error = None
            else:
                policy.is_synced = False
                policy.last_sync_error = message
        if success:
            pushed += 1
        else:
            errors.append(f'{mapping.linux_username}: {message}')

    db.session.commit()
    if errors and pushed == 0:
        return False, '; '.join(errors)
    if errors:
        return True, f'Pushed {pushed} mapping(s); partial failures: {"; ".join(errors)}'
    if pushed == 0:
        return True, 'No Android mappings required device policy sync'
    return True, f'Pushed device policy for {pushed} mapping(s)'
