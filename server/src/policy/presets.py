"""Age bracket × maturity policy preset orchestration."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from src.models import (
    AppPolicy,
    ManagedUser,
    ManagedUserDeviceMap,
    UserWeeklySchedule,
    db,
)

_LOGGER = logging.getLogger(__name__)

VALID_AGE_BRACKETS = frozenset({'under7', '8_12', '13_15', '16_plus'})
VALID_MATURITY_LEVELS = frozenset({'low', 'medium', 'high'})

AGE_BRACKET_TO_OVERLAY_TIER = {
    'under7': 'under8',
    '8_12': 'eight12',
    '13_15': 'teen',
    '16_plus': 'teen',
}

_WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday')
_WEEKENDS = ('saturday', 'sunday')


def load_policy_preset_matrix() -> dict:
    """Load the static age × maturity policy preset matrix."""
    matrix_path = os.path.join(os.path.dirname(__file__), 'policy_preset_matrix.json')
    try:
        with open(matrix_path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except Exception as exc:
        _LOGGER.error('Failed to load policy preset matrix JSON: %s', exc)
        return {'age_brackets': {}, 'maturity_levels': {}, 'bundles': {}}


def _bundle_key(age_bracket: str, maturity_level: str) -> str:
    return f'{age_bracket}_{maturity_level}'


def get_policy_bundle(age_bracket: str, maturity_level: str) -> dict:
    """Return the bundle definition for a valid age × maturity cell."""
    normalized_age = (age_bracket or '').strip()
    normalized_maturity = (maturity_level or '').strip()
    if normalized_age not in VALID_AGE_BRACKETS:
        raise ValueError(f'Invalid policy_age_bracket: {age_bracket}')
    if normalized_maturity not in VALID_MATURITY_LEVELS:
        raise ValueError(f'Invalid policy_maturity_level: {maturity_level}')

    matrix = load_policy_preset_matrix()
    bundle = (matrix.get('bundles') or {}).get(_bundle_key(normalized_age, normalized_maturity))
    if not bundle:
        raise ValueError(
            f'No policy preset bundle defined for {normalized_age} / {normalized_maturity}',
        )
    return bundle


def _schedule_dict_from_bundle(bundle: dict) -> dict:
    hours = bundle.get('weekly_schedule_hours') or {}
    weekday_hours = float(hours.get('weekday', 0))
    weekend_hours = float(hours.get('weekend', weekday_hours))
    schedule = {}
    for day in _WEEKDAYS:
        schedule[day] = weekday_hours
    for day in _WEEKENDS:
        schedule[day] = weekend_hours
    return schedule


def _is_linux_mapping(mapping: ManagedUserDeviceMap) -> bool:
    platform = (mapping.device.platform if mapping.device else None) or AppPolicy.PLATFORM_LINUX
    return platform != AppPolicy.PLATFORM_ANDROID


def _is_android_mapping(mapping: ManagedUserDeviceMap) -> bool:
    platform = (mapping.device.platform if mapping.device else None) or AppPolicy.PLATFORM_LINUX
    return platform == AppPolicy.PLATFORM_ANDROID


def _upsert_weekly_schedule(user: ManagedUser, bundle: dict) -> None:
    schedule_data = _schedule_dict_from_bundle(bundle)
    schedule = UserWeeklySchedule.query.filter_by(user_id=user.id).first()
    if schedule is None:
        schedule = UserWeeklySchedule(user_id=user.id)
        db.session.add(schedule)
    schedule.set_schedule_from_dict(schedule_data)


def apply_policy_preset(
    user: ManagedUser,
    age_bracket: str,
    maturity_level: str,
    *,
    mappings: list[ManagedUserDeviceMap] | None = None,
) -> dict:
    """
    Apply a composite policy preset to a managed child profile.

    Overwrites preset-controlled fields: marketplace blocklists, overlay tier,
    weekly schedule, Linux device policy, approval settings, and Android profile type.
    """
    from src.user.approvals import get_or_create_settings
    from src.policy.linux import upsert_policy
    from src.blocklist.marketplace import sync_marketplace_subscriptions

    bundle = get_policy_bundle(age_bracket, maturity_level)
    normalized_age = age_bracket.strip()
    normalized_maturity = maturity_level.strip()

    user = db.session.merge(user)

    user.policy_age_bracket = normalized_age
    user.policy_maturity_level = normalized_maturity
    user.overlay_age_tier = AGE_BRACKET_TO_OVERLAY_TIER[normalized_age]

    preset_ids = list(bundle.get('marketplace_preset_ids') or [])
    sync_marketplace_subscriptions(user, preset_ids)
    _upsert_weekly_schedule(user, bundle)

    if mappings is not None:
        target_mappings = [db.session.merge(m) for m in mappings]
    else:
        target_mappings = ManagedUserDeviceMap.query.filter_by(
            managed_user_id=user.id,
        ).all()
    linux_policy_body = bundle.get('linux_device_policy') or {}
    approval_body = bundle.get('approval_settings') or {}
    android_profile_type = bundle.get('android_profile_type')

    linux_mappings_updated = 0
    android_mappings_updated = 0

    for mapping in target_mappings:
        if _is_linux_mapping(mapping) and linux_policy_body:
            upsert_policy(mapping, linux_policy_body)
            linux_mappings_updated += 1
        if approval_body:
            settings = get_or_create_settings(mapping)
            if approval_body.get('app_launch_mode') is not None:
                settings.app_launch_mode = approval_body['app_launch_mode']
            if approval_body.get('domain_access_mode') is not None:
                settings.domain_access_mode = approval_body['domain_access_mode']
            if 'registration_approval_enabled' in approval_body:
                settings.registration_approval_enabled = bool(
                    approval_body['registration_approval_enabled'],
                )
        if (
            android_profile_type in ('restricted', 'standard')
            and _is_android_mapping(mapping)
        ):
            mapping.android_profile_type = android_profile_type
            android_mappings_updated += 1

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise ValueError(f'Failed to apply policy preset: {exc}') from exc

    affected_system_ids = {mapping.system_id for mapping in target_mappings if mapping.system_id}
    try:
        from app import task_manager
        task_manager.notify_domain_policy_hint(
            system_ids=affected_system_ids or None,
            reason='policy_preset_applied',
        )
    except Exception as exc:
        _LOGGER.error('Failed to notify policy sync hint after preset apply: %s', exc)

    return {
        'age_bracket': normalized_age,
        'maturity_level': normalized_maturity,
        'overlay_age_tier': user.overlay_age_tier,
        'marketplace_preset_ids': preset_ids,
        'weekly_schedule_hours': bundle.get('weekly_schedule_hours'),
        'linux_mappings_updated': linux_mappings_updated,
        'android_mappings_updated': android_mappings_updated,
        'applied_at': datetime.now(timezone.utc).isoformat(),
    }


def get_matrix_metadata_for_ui() -> dict:
    """Return matrix metadata and bundles for server-rendered templates."""
    matrix = load_policy_preset_matrix()
    return {
        'age_brackets': matrix.get('age_brackets') or {},
        'maturity_levels': matrix.get('maturity_levels') or {},
        'bundles': matrix.get('bundles') or {},
    }
