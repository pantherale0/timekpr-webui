"""Business logic for Linux device restriction policies (polkit + terminal exec)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from src.models import (
    AgentDevice,
    AppPolicy,
    db,
    ManagedUserDeviceMap,
    MappingLinuxDevicePolicy,
)

_LOGGER = logging.getLogger(__name__)


def _is_linux_mapping(mapping: ManagedUserDeviceMap) -> bool:
    platform = (mapping.device.platform if mapping.device else None) or AppPolicy.PLATFORM_LINUX
    return platform != AppPolicy.PLATFORM_ANDROID


def _require_linux_mapping(mapping: ManagedUserDeviceMap) -> None:
    if not _is_linux_mapping(mapping):
        raise ValueError('Linux device policy is only supported for Linux device mappings')


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


def _normalize_support_message(value) -> str:
    normalized = (value or '').strip()
    if not normalized:
        raise ValueError('support_message must not be empty')
    if len(normalized) > MappingLinuxDevicePolicy.MAX_SUPPORT_MESSAGE_LENGTH:
        raise ValueError(
            f'support_message exceeds maximum length of '
            f'{MappingLinuxDevicePolicy.MAX_SUPPORT_MESSAGE_LENGTH}',
        )
    return normalized


def _clean_allowed_extensions(value) -> list[str]:
    if value is None:
        return []
    
    raw_list = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                raw_list.append(item)
            else:
                raise ValueError('Extension ID must be a string')
    elif isinstance(value, str):
        raw_list = value.replace(',', ' ').split()
    else:
        raise ValueError('allowed_extension_ids must be a string or a list of strings')
        
    cleaned = []
    for ext_id in raw_list:
        trimmed = ext_id.strip()
        if not trimmed:
            continue
        if len(trimmed) != 32 or not all(c in 'abcdefghijklmnop' for c in trimmed):
            raise ValueError(f'Invalid Chrome Extension ID: "{trimmed}". Extension IDs must be exactly 32 alphabetical characters (a-p).')
        cleaned.append(trimmed)
    return cleaned

def build_device_policy_payload(policy: MappingLinuxDevicePolicy) -> dict:
    """Build canonical device policy JSON for Linux agent sync."""
    support_message = policy.support_message or MappingLinuxDevicePolicy.DEFAULT_SUPPORT_MESSAGE
    chrome_config = policy.chrome_policies
    return {
        'polkit': {
            'installSoftwareDisabled': bool(policy.install_software_disabled),
            'uninstallSoftwareDisabled': bool(policy.uninstall_software_disabled),
            'mountRemovableMediaDisabled': bool(policy.mount_removable_media_disabled),
            'modifyAccountsDisabled': bool(policy.modify_accounts_disabled),
            'systemPowerActionsDisabled': bool(policy.system_power_actions_disabled),
            'pkexecElevationDisabled': bool(policy.pkexec_elevation_disabled),
            'flatpakInstallDisabled': bool(policy.flatpak_install_disabled),
            'snapInstallDisabled': bool(policy.snap_install_disabled),
        },
        'connectivity': {
            'bluetoothDisabled': bool(policy.bluetooth_disabled),
        },
        'exec': {
            'terminalAccessDisabled': bool(policy.terminal_access_disabled),
        },
        'chrome': {
            'incognitoDisabled': bool(chrome_config.get('incognito_disabled', True)),
            'safeBrowsingEnforced': bool(chrome_config.get('safesearch_enforced', True)),
            'youtubeRestrict': int(chrome_config.get('youtube_restrict', 2)),
            'blockOtherExtensions': bool(chrome_config.get('block_other_extensions', False)),
            'blockGenaiFeatures': bool(chrome_config.get('block_genai_features', False)),
            'allowedExtensionIds': list(chrome_config.get('allowed_extension_ids', [])),
        },
        'supportMessage': support_message,
    }


def compute_revision(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _get_policy_row(mapping: ManagedUserDeviceMap) -> MappingLinuxDevicePolicy | None:
    return MappingLinuxDevicePolicy.query.filter_by(device_map_id=mapping.id).first()


def get_or_create_policy(mapping: ManagedUserDeviceMap) -> MappingLinuxDevicePolicy:
    _require_linux_mapping(mapping)
    policy = _get_policy_row(mapping)
    if policy is None:
        payload = build_device_policy_payload(
            MappingLinuxDevicePolicy(device_map_id=mapping.id),
        )
        policy = MappingLinuxDevicePolicy(
            device_map_id=mapping.id,
            revision=compute_revision(payload),
        )
        db.session.add(policy)
        db.session.flush()
    return policy


def build_policy_summary(policy: MappingLinuxDevicePolicy, mapping: ManagedUserDeviceMap) -> dict:
    device = mapping.device
    return {
        'device_map_id': mapping.id,
        'system_id': mapping.system_id,
        'linux_username': mapping.linux_username,
        'device_label': (device.system_hostname if device else None) or mapping.system_id,
        'platform': (device.platform if device else None) or AppPolicy.PLATFORM_LINUX,
        'install_software_disabled': policy.install_software_disabled,
        'uninstall_software_disabled': policy.uninstall_software_disabled,
        'mount_removable_media_disabled': policy.mount_removable_media_disabled,
        'modify_accounts_disabled': policy.modify_accounts_disabled,
        'system_power_actions_disabled': policy.system_power_actions_disabled,
        'pkexec_elevation_disabled': policy.pkexec_elevation_disabled,
        'bluetooth_disabled': policy.bluetooth_disabled,
        'flatpak_install_disabled': policy.flatpak_install_disabled,
        'snap_install_disabled': policy.snap_install_disabled,
        'terminal_access_disabled': policy.terminal_access_disabled,
        'chrome_policies': policy.chrome_policies,
        'support_message': (
            policy.support_message or MappingLinuxDevicePolicy.DEFAULT_SUPPORT_MESSAGE
        ),
        'revision': policy.revision,
        'is_synced': policy.is_synced,
        'last_synced_at': policy.last_synced_at.isoformat() if policy.last_synced_at else None,
        'last_sync_error': policy.last_sync_error,
        'device_policy': build_device_policy_payload(policy),
    }


def upsert_policy(mapping: ManagedUserDeviceMap, body: dict) -> MappingLinuxDevicePolicy:
    _require_linux_mapping(mapping)
    if not isinstance(body, dict):
        raise ValueError('Request body must be a JSON object')

    policy = get_or_create_policy(mapping)

    for bool_field in (
        'install_software_disabled',
        'uninstall_software_disabled',
        'mount_removable_media_disabled',
        'modify_accounts_disabled',
        'system_power_actions_disabled',
        'pkexec_elevation_disabled',
        'bluetooth_disabled',
        'flatpak_install_disabled',
        'snap_install_disabled',
        'terminal_access_disabled',
    ):
        if bool_field in body:
            setattr(
                policy,
                bool_field,
                _coerce_bool(body.get(bool_field), bool_field),
            )
    if 'support_message' in body:
        policy.support_message = _normalize_support_message(body.get('support_message'))

    if 'chrome_policies' in body:
        chrome_req = body.get('chrome_policies') or {}
        if not isinstance(chrome_req, dict):
            raise ValueError('chrome_policies must be a JSON object')

        current = policy.chrome_policies

        incognito = chrome_req.get('incognito_disabled')
        if incognito is not None:
            current['incognito_disabled'] = _coerce_bool(incognito, 'incognito_disabled')

        safesearch = chrome_req.get('safesearch_enforced')
        if safesearch is not None:
            current['safesearch_enforced'] = _coerce_bool(safesearch, 'safesearch_enforced')

        youtube = chrome_req.get('youtube_restrict')
        if youtube is not None:
            try:
                youtube_val = int(youtube)
                if youtube_val not in (0, 1, 2):
                    raise ValueError
                current['youtube_restrict'] = youtube_val
            except (TypeError, ValueError):
                raise ValueError('youtube_restrict must be 0, 1, or 2')

        block_ext = chrome_req.get('block_other_extensions')
        if block_ext is not None:
            current['block_other_extensions'] = _coerce_bool(block_ext, 'block_other_extensions')

        block_genai = chrome_req.get('block_genai_features')
        if block_genai is not None:
            current['block_genai_features'] = _coerce_bool(block_genai, 'block_genai_features')

        allowed_exts = chrome_req.get('allowed_extension_ids')
        if allowed_exts is not None:
            current['allowed_extension_ids'] = _clean_allowed_extensions(allowed_exts)

        policy.chrome_policies = current

    payload = build_device_policy_payload(policy)
    policy.revision = compute_revision(payload)
    policy.is_synced = False
    policy.last_sync_error = None
    policy.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    push_success, push_message = push_mapping_device_policy(mapping)
    if push_success and push_message and 'Queued' not in push_message:
        policy.is_synced = True
        policy.last_synced_at = datetime.now(timezone.utc)
        policy.last_sync_error = None
    else:
        policy.is_synced = False
        policy.last_sync_error = push_message
    db.session.commit()
    return policy


def push_mapping_device_policy(mapping: ManagedUserDeviceMap) -> tuple[bool, str]:
    """Push device restriction policy to the Linux agent when online."""
    from src.agent.helper import AgentClient, AgentConnectionManager

    if not _is_linux_mapping(mapping):
        return False, 'Not a Linux mapping'

    policy = _get_policy_row(mapping)
    if policy is None:
        policy = get_or_create_policy(mapping)
        db.session.commit()

    if not AgentConnectionManager.is_online(mapping.system_id):
        from src.agent.pending_commands import enqueue_policy_snapshot

        try:
            enqueue_policy_snapshot(
                mapping.system_id,
                'sync_linux_device_policy',
                mapping.linux_username,
            )
            return True, 'Queued for reconnect'
        except ValueError as exc:
            return False, str(exc)

    payload = build_device_policy_payload(policy)
    agent = AgentClient(system_id=mapping.system_id)
    return agent.sync_linux_device_policy(mapping.linux_username, payload)


def sync_linux_device_policies_for_system(system_id: str) -> tuple[bool, str]:
    """Push device policies for all Linux mappings on a connected device."""
    device = AgentDevice.query.get(system_id)
    if device is None or (device.platform or AppPolicy.PLATFORM_LINUX) == AppPolicy.PLATFORM_ANDROID:
        return True, 'No Linux device policy sync required'

    mappings = ManagedUserDeviceMap.query.filter_by(system_id=system_id).all()
    if not mappings:
        return True, 'No mappings for device'

    pushed = 0
    errors = []
    for mapping in mappings:
        if not _is_linux_mapping(mapping):
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
        return True, 'No Linux mappings required device policy sync'
    return True, f'Pushed device policy for {pushed} mapping(s)'
