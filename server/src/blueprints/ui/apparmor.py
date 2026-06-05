import logging
from flask import Blueprint, session, redirect, url_for, flash, render_template, request
from src.database import (
    db,
    ManagedUserDeviceMap,
    AppArmorRule,
    ManagedUser,
    AppPolicy,
    AppPolicyRule,
    ManagedUserAppPolicyAssignment,
)
from src.agent_helper import AgentConnectionManager, AgentClient
from src.helpers import _get_device_label_map
from src.installed_apps_manager import list_discovered_apps_for_platform
from src.apparmor_manager import (
    CURATED_APPARMOR_APPS,
    CURATED_APPARMOR_PATHS,
    _get_apparmor_usage_summary,
    compile_user_apparmor_rules,
    validate_policy_rule_for_platform,
    _normalize_policy_platform,
    _build_apparmor_policy_sync_payload,
)

_LOGGER = logging.getLogger(__name__)

ui_apparmor_bp = Blueprint('ui_apparmor', __name__)


def sync_policy_to_all_assigned_users(policy):
    """Compile rules and sync to all managed users assigned to this policy."""
    device_labels = _get_device_label_map()
    
    for assignment in policy.assignments:
        user = assignment.managed_user
        # Compile rules first
        compile_user_apparmor_rules(user)
        
        # Now sync down to all linked devices of this user
        for mapping in user.device_mappings:
            policies_list, skipped_rule_names = _build_apparmor_policy_sync_payload(mapping)
            if AgentConnectionManager.is_online(mapping.system_id):
                agent = AgentClient(system_id=mapping.system_id)
                device_label = device_labels.get(mapping.system_id, mapping.system_id)
                success, sync_msg = agent.sync_apparmor_policy(
                    mapping.linux_username,
                    policies_list,
                )
                if success:
                    _LOGGER.info("Synced AppArmor policy to %s on %s", mapping.linux_username, device_label)
                else:
                    _LOGGER.warning("Failed to sync AppArmor policy to %s on %s: %s", mapping.linux_username, device_label, sync_msg)


@ui_apparmor_bp.route('/admin/app-policies', methods=['GET'])
def admin_app_policies():
    """Visual admin panel for reusable App Policies."""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))

    policies = AppPolicy.query.order_by(AppPolicy.name.asc()).all()
    managed_users = ManagedUser.query.order_by(ManagedUser.username.asc()).all()
    curated_options = CURATED_APPARMOR_APPS
    discovered_apps_by_policy = {
        policy.id: list_discovered_apps_for_platform(policy.platform)
        for policy in policies
    }

    return render_template(
        'restrictions_app.html',
        policies=policies,
        managed_users=managed_users,
        curated_options=curated_options,
        discovered_apps_by_policy=discovered_apps_by_policy,
    )


@ui_apparmor_bp.route('/admin/app-policies/create', methods=['POST'])
def create_app_policy():
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))

    name = (request.form.get('name') or '').strip()
    platform_raw = (request.form.get('platform') or AppPolicy.PLATFORM_LINUX).strip()
    if not name:
        flash('Policy name is required', 'danger')
        return redirect(url_for('ui_apparmor.admin_app_policies'))

    try:
        platform = _normalize_policy_platform(platform_raw)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('ui_apparmor.admin_app_policies'))

    existing = AppPolicy.query.filter_by(name=name).first()
    if existing:
        flash(f'Policy "{name}" already exists', 'warning')
        return redirect(url_for('ui_apparmor.admin_app_policies'))

    policy = AppPolicy(name=name, platform=platform)
    db.session.add(policy)
    db.session.commit()

    flash(f'App Policy "{name}" created successfully', 'success')
    return redirect(url_for('ui_apparmor.admin_app_policies'))


@ui_apparmor_bp.route('/admin/app-policies/<int:policy_id>/delete', methods=['POST'])
def delete_app_policy(policy_id):
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))

    policy = AppPolicy.query.get_or_404(policy_id)
    policy_name = policy.name

    # Collect affected users for compilation & sync
    affected_users = [assignment.managed_user for assignment in policy.assignments if assignment.managed_user]

    db.session.delete(policy)
    db.session.commit()

    # Sync changes to affected users
    for user in affected_users:
        compile_user_apparmor_rules(user)
        for mapping in user.device_mappings:
            policies_list, _ = _build_apparmor_policy_sync_payload(mapping)
            if AgentConnectionManager.is_online(mapping.system_id):
                agent = AgentClient(system_id=mapping.system_id)
                agent.sync_apparmor_policy(mapping.linux_username, policies_list)

    flash(f'App Policy "{policy_name}" deleted successfully', 'success')
    return redirect(url_for('ui_apparmor.admin_app_policies'))


@ui_apparmor_bp.route('/admin/app-policies/<int:policy_id>/rule/add', methods=['POST'])
def add_app_policy_rule(policy_id):
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))

    policy = AppPolicy.query.get_or_404(policy_id)

    app_name = (request.form.get('application_name') or '').strip()
    match_type = (request.form.get('match_type') or AppPolicyRule.MATCH_TYPE_EXECUTABLE).strip()
    path = (request.form.get('executable_path') or '').strip()
    preset = (request.form.get('preset') or AppPolicyRule.PRESET_ALLOWED).strip()

    # Prepopulate if curated or discovered app option chosen
    curated_path = request.form.get('curated_app_choice')
    discovered_key = (request.form.get('discovered_app_choice') or '').strip()
    if discovered_key:
        for app in list_discovered_apps_for_platform(policy.platform):
            app_key = f"{app['match_type']}|{app['identifier']}"
            if app_key == discovered_key:
                app_name = app['application_name']
                path = app['identifier']
                match_type = app['match_type']
                break
    elif curated_path and policy.platform == AppPolicy.PLATFORM_LINUX:
        for app in CURATED_APPARMOR_APPS:
            if app['path'] == curated_path:
                app_name = app['name']
                path = app['path']
                match_type = AppArmorRule.MATCH_TYPE_EXECUTABLE
                break

    if not app_name or not path:
        flash('Application name and target are required', 'danger')
        return redirect(url_for('ui_apparmor.admin_app_policies'))

    try:
        _, match_type, preset, path = validate_policy_rule_for_platform(
            policy.platform,
            match_type,
            preset,
            path,
        )
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('ui_apparmor.admin_app_policies'))

    # Duplicate or update rule check
    existing = AppPolicyRule.query.filter_by(policy_id=policy.id, executable_path=path).first()
    if existing:
        existing.preset = preset
        existing.application_name = app_name
        existing.match_type = match_type
    else:
        rule = AppPolicyRule(
            policy_id=policy.id,
            application_name=app_name,
            executable_path=path,
            match_type=match_type,
            preset=preset,
            is_custom=True
        )
        db.session.add(rule)

    db.session.commit()

    # Sync changes to all assigned users
    sync_policy_to_all_assigned_users(policy)

    flash(f'Added rule for "{app_name}" to policy "{policy.name}"', 'success')
    return redirect(url_for('ui_apparmor.admin_app_policies'))


@ui_apparmor_bp.route('/admin/app-policies/rule/<int:rule_id>/delete', methods=['POST'])
def delete_app_policy_rule(rule_id):
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))

    rule = AppPolicyRule.query.get_or_404(rule_id)
    policy = rule.policy
    app_name = rule.application_name

    db.session.delete(rule)
    db.session.commit()

    # Sync changes to all assigned users
    sync_policy_to_all_assigned_users(policy)

    flash(f'Removed rule for "{app_name}" from policy "{policy.name}"', 'success')
    return redirect(url_for('ui_apparmor.admin_app_policies'))


@ui_apparmor_bp.route('/managed-users/<int:user_id>/app-policies/update', methods=['POST'])
def update_user_app_policies(user_id):
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))

    user = ManagedUser.query.get_or_404(user_id)
    selected_ids = {
        int(raw_id)
        for raw_id in request.form.getlist('policy_ids')
        if raw_id.strip().isdigit()
    }

    valid_policies = {
        policy.id: policy
        for policy in AppPolicy.query.filter(AppPolicy.id.in_(selected_ids)).all()
    } if selected_ids else {}

    if selected_ids and len(valid_policies) != len(selected_ids):
        flash('One or more selected app policies no longer exist', 'danger')
        return redirect(url_for('ui_dashboard.edit_user_profile', user_id=user.id))

    current_ids = {assignment.policy_id for assignment in user.app_policy_assignments}
    for assignment in list(user.app_policy_assignments):
        if assignment.policy_id not in selected_ids:
            db.session.delete(assignment)

    for policy_id in sorted(selected_ids - current_ids):
        db.session.add(ManagedUserAppPolicyAssignment(managed_user_id=user.id, policy_id=policy_id))

    db.session.commit()

    # Compile rules for this user now
    compile_user_apparmor_rules(user)

    # Sync down to all linked devices of this user
    from src.agent_helper import AgentConnectionManager, AgentClient
    from src.helpers import _get_device_label_map
    device_labels = _get_device_label_map()

    sync_count = 0
    fail_count = 0
    for mapping in user.device_mappings:
        policies_list, _ = _build_apparmor_policy_sync_payload(mapping)
        if AgentConnectionManager.is_online(mapping.system_id):
            agent = AgentClient(system_id=mapping.system_id)
            device_label = device_labels.get(mapping.system_id, mapping.system_id)
            success, sync_msg = agent.sync_apparmor_policy(
                mapping.linux_username,
                policies_list,
            )
            if success:
                sync_count += 1
            else:
                fail_count += 1

    if sync_count > 0 and fail_count == 0:
        flash(f'Updated app policies for {user.username} and synced to online devices', 'success')
    elif fail_count > 0:
        flash(f'Updated app policies for {user.username}, but sync failed for some devices', 'warning')
    else:
        flash(f'Updated app policies for {user.username}. Will sync when devices reconnect.', 'success')

    return redirect(url_for('ui_dashboard.edit_user_profile', user_id=user.id))
