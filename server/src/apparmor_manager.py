import logging
from datetime import datetime, timezone, timedelta
from src.database import (
    db,
    AppArmorRule,
    AppUsageHistory,
    ManagedUserDeviceMap,
    AppPolicy,
    AppPolicyRule,
    ManagedUserAppPolicyAssignment,
)

_LOGGER = logging.getLogger(__name__)

APPARMOR_UNSAFE_EXECUTABLE_PATH_CHARS = set('*?[]{}"')
APPARMOR_ALLOWED_PATH_PATTERN_PREFIXES = ('$HOME/', '/home/$USER/')
APPARMOR_PATH_PATTERN_SUFFIX = '/**'

CURATED_APPARMOR_APPS = [
    {'name': 'Firefox',            'path': '/usr/bin/firefox',            'icon': '🦊'},
    {'name': 'Google Chrome',      'path': '/usr/bin/google-chrome',      'icon': '🌐'},
    {'name': 'Steam',              'path': '/usr/bin/steam',              'icon': '🎮'},
    {'name': 'Discord',            'path': '/usr/bin/discord',            'icon': '💬'},
    {'name': 'Minecraft',          'path': '/usr/bin/minecraft-launcher', 'icon': '⛏️'},
    {'name': 'Spotify',            'path': '/usr/bin/spotify',            'icon': '🎵'},
    {'name': 'VLC',                'path': '/usr/bin/vlc',                'icon': '🎬'},
]

CURATED_APPARMOR_PATHS = {app['path'] for app in CURATED_APPARMOR_APPS}


def _validate_apparmor_executable_path(executable_path):
    """Allow only concrete absolute executable paths, not AppArmor globs."""
    normalized = (executable_path or '').strip()
    if not normalized:
        raise ValueError('Executable path is required')
    if not normalized.startswith('/'):
        raise ValueError('Executable path must be an absolute path like /usr/bin/firefox')
    if normalized.endswith('/'):
        raise ValueError('Executable path must point to a single executable, not a directory')
    if any(char in normalized for char in APPARMOR_UNSAFE_EXECUTABLE_PATH_CHARS):
        raise ValueError(
            'Executable path must be a single concrete executable; glob patterns like /usr/bin/** are not allowed'
        )
    if any(char.isspace() for char in normalized):
        raise ValueError('Executable paths with spaces are not supported')
    return normalized


def _validate_apparmor_path_pattern(path_pattern, linux_username):
    """Allow only home-directory subtree patterns like $HOME/Downloads/**."""
    normalized = (path_pattern or '').strip()
    if not normalized:
        raise ValueError('Path pattern is required')
    if any(char.isspace() for char in normalized):
        raise ValueError('Path patterns with spaces are not supported')

    explicit_home_prefix = f'/home/{linux_username}/'
    if normalized.startswith(explicit_home_prefix):
        normalized = '$HOME/' + normalized[len(explicit_home_prefix):]
    elif normalized.startswith('/home/$USER/'):
        normalized = '$HOME/' + normalized[len('/home/$USER/'):]

    if not normalized.startswith(APPARMOR_ALLOWED_PATH_PATTERN_PREFIXES):
        raise ValueError('Path patterns must stay under $HOME/ or /home/$USER/')
    if not normalized.endswith(APPARMOR_PATH_PATTERN_SUFFIX):
        raise ValueError('Path patterns must target a subtree and end with /**')

    root = normalized[:-len(APPARMOR_PATH_PATTERN_SUFFIX)]
    if not root or root in {'$HOME', '/home/$USER'}:
        return normalized
    if '*' in root or '?' in root or '[' in root or ']' in root or '{' in root or '}' in root or '"' in root:
        raise ValueError('Only a trailing /** glob is supported for path rules')
    if '/./' in normalized or '/../' in normalized or normalized.endswith('/..') or normalized.endswith('/.'):
        raise ValueError('Path patterns must not contain relative path segments')
    return normalized


def _validate_apparmor_rule_target(match_type, target_value, linux_username):
    if match_type == AppArmorRule.MATCH_TYPE_PATH_PATTERN:
        return _validate_apparmor_path_pattern(target_value, linux_username)
    return _validate_apparmor_executable_path(target_value)


def _is_valid_preset_for_match_type(match_type, preset):
    if match_type == AppArmorRule.MATCH_TYPE_PATH_PATTERN:
        return preset in {
            AppArmorRule.PRESET_ALLOWED,
            AppArmorRule.PRESET_BLOCKED,
            AppArmorRule.PRESET_COMPLAIN,
        }
    return preset in AppArmorRule.VALID_PRESETS


def _build_apparmor_policy_sync_payload(mapping):
    """Collect restrictive AppArmor rules for a mapping and sanitize them for sync."""
    all_rules = AppArmorRule.query.filter_by(device_map_id=mapping.id).all()
    policies_list = []
    skipped_rule_names = []
    for rule in all_rules:
        if not rule.is_restrictive:
            continue
        try:
            _validate_apparmor_rule_target(rule.match_type, rule.executable_path, mapping.linux_username)
            if not _is_valid_preset_for_match_type(rule.match_type, rule.preset):
                raise ValueError('preset is not supported for this rule type')
        except ValueError:
            skipped_rule_names.append(rule.application_name or rule.executable_path)
            continue
        policies_list.append(rule.to_sync_dict())
    return policies_list, skipped_rule_names


def _store_app_usage_from_alert(system_id, normalized_alert):
    """Persist a structured AppUsageHistory row from an app_usage alert event."""
    details = normalized_alert.get('details', {})
    if not isinstance(details, dict):
        return

    linux_username = normalized_alert.get('linux_username')
    executable_path = (details.get('executable_path') or '').strip()
    application_name = (details.get('application_name') or '').strip() or executable_path
    duration_seconds = details.get('duration_seconds')

    if not linux_username or not executable_path or not isinstance(duration_seconds, (int, float)):
        return

    duration_seconds = max(0, int(duration_seconds))
    mapping = ManagedUserDeviceMap.query.filter_by(
        system_id=system_id,
        linux_username=linux_username,
    ).first()
    if not mapping:
        return

    start_iso = (details.get('start_time') or '').strip()
    end_iso = (details.get('end_time') or '').strip()
    try:
        start_time = datetime.fromisoformat(start_iso.replace('Z', '+00:00')).replace(tzinfo=None)
        end_time = datetime.fromisoformat(end_iso.replace('Z', '+00:00')).replace(tzinfo=None)
    except (TypeError, ValueError):
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(seconds=duration_seconds)

    record = AppUsageHistory(
        device_map_id=mapping.id,
        application_name=application_name,
        executable_path=executable_path,
        start_time=start_time,
        end_time=end_time,
        duration_seconds=duration_seconds,
    )
    db.session.add(record)
    db.session.commit()
    _LOGGER.info(
        "Stored app_usage record for %s@%s: %s (%ds)",
        linux_username, system_id, application_name, duration_seconds,
    )


def _get_apparmor_usage_summary(mapping_id, days=7):
    """Build an aggregate app-usage summary for a mapping over the last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    records = AppUsageHistory.query.filter(
        AppUsageHistory.device_map_id == mapping_id,
        AppUsageHistory.start_time >= cutoff,
    ).all()

    aggregate = {}
    for record in records:
        key = record.executable_path
        entry = aggregate.setdefault(key, {
            'application_name': record.application_name,
            'executable_path': record.executable_path,
            'total_seconds': 0,
            'session_count': 0,
        })
        entry['total_seconds'] += record.duration_seconds
        entry['session_count'] += 1

    result = sorted(aggregate.values(), key=lambda item: -item['total_seconds'])
    for item in result:
        secs = item['total_seconds']
        hours = secs // 3600
        minutes = (secs % 3600) // 60
        if hours > 0:
            item['formatted'] = f"{hours}h {minutes}m"
        else:
            item['formatted'] = f"{minutes}m"
    return result


def compile_user_apparmor_rules(user):
    """Compile assigned AppPolicies and rules into AppArmorRule records for all user mappings."""
    assigned_policies = [assignment.policy for assignment in user.app_policy_assignments]
    
    preset_priority = {
        'blocked': 4,
        'no_internet': 3,
        'complain': 2,
        'allowed': 1
    }
    
    compiled = {}
    
    for policy in assigned_policies:
        if not policy.rules:
            continue
        for rule in policy.rules:
            path = rule.executable_path
            current_priority = preset_priority.get(rule.preset, 0)
            
            if path in compiled:
                existing_priority = preset_priority.get(compiled[path]['preset'], 0)
                if current_priority > existing_priority:
                    compiled[path] = {
                        'application_name': rule.application_name,
                        'match_type': rule.match_type,
                        'preset': rule.preset,
                        'is_custom': rule.is_custom
                    }
            else:
                compiled[path] = {
                    'application_name': rule.application_name,
                    'match_type': rule.match_type,
                    'preset': rule.preset,
                    'is_custom': rule.is_custom
                }
                
    for mapping in user.device_mappings:
        AppArmorRule.query.filter_by(device_map_id=mapping.id).delete()
        
        for path, rule_data in compiled.items():
            db_rule = AppArmorRule(
                device_map_id=mapping.id,
                application_name=rule_data['application_name'],
                executable_path=path,
                match_type=rule_data['match_type'],
                preset=rule_data['preset'],
                is_custom=rule_data['is_custom']
            )
            db.session.add(db_rule)
            
    db.session.commit()
    _LOGGER.info("Successfully compiled %d app policy rules for child %s", len(compiled), user.username)

