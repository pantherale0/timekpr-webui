"""Android packages and helpers for anti-bypass hardening via policy presets."""

from __future__ import annotations

from src.models import (
    AppPolicy,
    AppPolicyRule,
    ManagedUser,
    ManagedUserAppPolicyAssignment,
    db,
)

BYPASS_POLICY_NAME_PREFIX = 'Anti-bypass tools'

# Profile creators, DPC sandboxes, Tasker shortcuts, Samsung Internet sideload vector.
ANDROID_BYPASS_TOOL_PACKAGES = (
    'com.oasisfeng.island',
    'net.typeblog.shelter',
    'com.afwsamples.testdpc',
    'net.dinglisch.android.taskerm',
    'com.sec.android.app.sbrowser',
)


def bypass_packages_for_maturity(maturity_level: str) -> list[str]:
    """Return bypass-tool packages to block for medium/high maturity presets."""
    normalized = (maturity_level or '').strip().lower()
    if normalized in {'medium', 'high'}:
        return list(ANDROID_BYPASS_TOOL_PACKAGES)
    return []


def _policy_name_for_user(username: str) -> str:
    return f'{BYPASS_POLICY_NAME_PREFIX} ({username})'


def apply_android_bypass_app_blocks(user: ManagedUser, maturity_level: str) -> int:
    """
    Sync preset-managed blocked rules for known bypass tools on Android app policies.

    Returns the number of package rules written.
    """
    packages = bypass_packages_for_maturity(maturity_level)
    policy_name = _policy_name_for_user(user.username)
    policy = AppPolicy.query.filter_by(
        name=policy_name,
        platform=AppPolicy.PLATFORM_ANDROID,
    ).first()

    if not packages:
        if policy is not None:
            for rule in list(policy.rules):
                db.session.delete(rule)
            assignment = ManagedUserAppPolicyAssignment.query.filter_by(
                managed_user_id=user.id,
                policy_id=policy.id,
            ).first()
            if assignment is not None:
                db.session.delete(assignment)
            db.session.delete(policy)
        return 0

    if policy is None:
        policy = AppPolicy(name=policy_name, platform=AppPolicy.PLATFORM_ANDROID)
        db.session.add(policy)
        db.session.flush()

    assignment = ManagedUserAppPolicyAssignment.query.filter_by(
        managed_user_id=user.id,
        policy_id=policy.id,
    ).first()
    if assignment is None:
        db.session.add(
            ManagedUserAppPolicyAssignment(
                managed_user_id=user.id,
                policy_id=policy.id,
            ),
        )

    desired_paths = {
        f'/android/package/{package_name}' for package_name in packages
    }
    existing_by_path = {rule.executable_path: rule for rule in policy.rules}

    for package_name in packages:
        executable_path = f'/android/package/{package_name}'
        if executable_path in existing_by_path:
            continue
        db.session.add(
            AppPolicyRule(
                policy_id=policy.id,
                application_name=package_name,
                executable_path=executable_path,
                match_type=AppPolicyRule.MATCH_TYPE_PACKAGE,
                preset=AppPolicyRule.PRESET_BLOCKED,
                is_custom=False,
            ),
        )

    for path, rule in list(existing_by_path.items()):
        if path not in desired_paths:
            db.session.delete(rule)

    return len(packages)


def sync_android_app_policies_for_user(user: ManagedUser) -> int:
    """Recompile and push Android app policies for every mapping on this child profile."""
    from src.policy.apparmor import compile_user_apparmor_rules
    from src.blueprints.ui.apparmor import _sync_mapping_app_policy_to_agent

    compile_user_apparmor_rules(user)
    synced = 0
    for mapping in user.device_mappings:
        platform = (mapping.device.platform if mapping.device else None) or AppPolicy.PLATFORM_LINUX
        if platform != AppPolicy.PLATFORM_ANDROID:
            continue
        _sync_mapping_app_policy_to_agent(mapping)
        synced += 1
    return synced
