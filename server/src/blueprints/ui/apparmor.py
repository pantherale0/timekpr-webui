import logging
from flask import Blueprint, session, redirect, url_for, render_template, request, jsonify
from src.i18n.catalog import flash_t, api_message
from src.common.helpers import wants_json_response
from src.models import (
    db,
    ManagedUserDeviceMap,
    AppArmorRule,
    ManagedUser,
    AppPolicy,
    AppPolicyRule,
    ManagedUserAppPolicyAssignment,
)
from src.agent.helper import AgentConnectionManager, AgentClient
from src.common.helpers import _get_device_label_map
from src.device.installed_apps import list_discovered_apps_for_platform
from src.policy.apparmor import (
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


def _sync_mapping_app_policy_to_agent(mapping):
    """Push restrictive rules plus approval overlay to an online agent."""
    from src.user.approvals import build_full_app_policy_sync_payload

    policies_list, _, approval_policy = build_full_app_policy_sync_payload(mapping)
    if not AgentConnectionManager.is_online(mapping.system_id):
        return False, 'offline'
    agent = AgentClient(system_id=mapping.system_id)
    return agent.sync_apparmor_policy(
        mapping.linux_username,
        policies_list,
        approval_policy=approval_policy,
    )


def sync_policy_to_all_assigned_users(policy):
    """Compile rules and sync to all managed users assigned to this policy."""
    device_labels = _get_device_label_map()
    
    for assignment in policy.assignments:
        user = assignment.managed_user
        # Compile rules first
        compile_user_apparmor_rules(user)
        
        # Now sync down to all linked devices of this user
        for mapping in user.device_mappings:
            device_label = device_labels.get(mapping.system_id, mapping.system_id)
            success, sync_msg = _sync_mapping_app_policy_to_agent(mapping)
            if success:
                _LOGGER.info("Synced AppArmor policy to %s on %s", mapping.linux_username, device_label)
            elif sync_msg != 'offline':
                _LOGGER.warning("Failed to sync AppArmor policy to %s on %s: %s", mapping.linux_username, device_label, sync_msg)


@ui_apparmor_bp.route('/admin/app-policies', methods=['GET'])
def admin_app_policies():
    """Serve the visual admin panel for reusable App Policies."""
    from src.blueprints.ui.spa import render_spa_shell
    return render_spa_shell('admin/app-policies')


@ui_apparmor_bp.route('/admin/app-policies/create', methods=['POST'])
def create_app_policy():
    if not session.get('logged_in'):
        flash_t('flash.auth.login_required', 'warning')
        return redirect(url_for('ui_auth.login'))

    name = (request.form.get('name') or '').strip()
    platform_raw = (request.form.get('platform') or AppPolicy.PLATFORM_LINUX).strip()
    if not name:
        flash_t('flash.apparmor.policy_name_required', 'danger')
        return redirect(url_for('ui_apparmor.admin_app_policies'))

    try:
        platform = _normalize_policy_platform(platform_raw)
    except ValueError as exc:
        flash_t('flash.common.generic_error', 'danger', error=str(exc))
        return redirect(url_for('ui_apparmor.admin_app_policies'))

    existing = AppPolicy.query.filter_by(name=name).first()
    if existing:
        flash_t('flash.apparmor.policy_exists', 'warning', name=name)
        return redirect(url_for('ui_apparmor.admin_app_policies'))

    policy = AppPolicy(name=name, platform=platform)
    db.session.add(policy)
    db.session.commit()

    flash_t('flash.apparmor.policy_created', 'success', name=name)
    return redirect(url_for('ui_apparmor.admin_app_policies'))


@ui_apparmor_bp.route('/admin/app-policies/<int:policy_id>/delete', methods=['POST'])
def delete_app_policy(policy_id):
    if not session.get('logged_in'):
        flash_t('flash.auth.login_required', 'warning')
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
            _sync_mapping_app_policy_to_agent(mapping)

    flash_t('flash.apparmor.policy_deleted', 'success', policy_name=policy_name)
    return redirect(url_for('ui_apparmor.admin_app_policies'))


@ui_apparmor_bp.route('/admin/app-policies/<int:policy_id>/rule/add', methods=['POST'])
def add_app_policy_rule(policy_id):
    if not session.get('logged_in'):
        flash_t('flash.auth.login_required', 'warning')
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
        flash_t('flash.apparmor.app_name_required', 'danger')
        return redirect(url_for('ui_apparmor.admin_app_policies'))

    try:
        _, match_type, preset, path = validate_policy_rule_for_platform(
            policy.platform,
            match_type,
            preset,
            path,
        )
    except ValueError as exc:
        flash_t('flash.common.generic_error', 'danger', error=str(exc))
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

    flash_t(
        'flash.apparmor.rule_added',
        'success',
        app_name=app_name,
        policy_name=policy.name,
    )
    return redirect(url_for('ui_apparmor.admin_app_policies'))


@ui_apparmor_bp.route('/admin/app-policies/rule/<int:rule_id>/delete', methods=['POST'])
def delete_app_policy_rule(rule_id):
    if not session.get('logged_in'):
        flash_t('flash.auth.login_required', 'warning')
        return redirect(url_for('ui_auth.login'))

    rule = AppPolicyRule.query.get_or_404(rule_id)
    policy = rule.policy
    app_name = rule.application_name

    db.session.delete(rule)
    db.session.commit()

    # Sync changes to all assigned users
    sync_policy_to_all_assigned_users(policy)

    flash_t(
        'flash.apparmor.rule_removed',
        'success',
        app_name=app_name,
        policy_name=policy.name,
    )
    return redirect(url_for('ui_apparmor.admin_app_policies'))


@ui_apparmor_bp.route('/managed-users/<int:user_id>/app-policies/update', methods=['POST'])
def update_user_app_policies(user_id):
    if not session.get('logged_in'):
        flash_t('flash.auth.login_required', 'warning')
        return redirect(url_for('ui_auth.login'))

    from src.common.helpers import check_parent_child_access
    check_parent_child_access(user_id)

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
        if wants_json_response():
            return jsonify({'success': False, 'message': api_message('apparmor_policies_missing')}), 400
        flash_t('flash.apparmor.policies_missing', 'danger')
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
    from src.agent.helper import AgentConnectionManager, AgentClient
    from src.common.helpers import _get_device_label_map
    device_labels = _get_device_label_map()

    sync_count = 0
    fail_count = 0
    for mapping in user.device_mappings:
        device_label = device_labels.get(mapping.system_id, mapping.system_id)
        success, sync_msg = _sync_mapping_app_policy_to_agent(mapping)
        if success:
            sync_count += 1
        elif sync_msg != 'offline':
            fail_count += 1

    sync_pending = fail_count > 0 or (len(user.device_mappings) > 0 and sync_count == 0)
    if wants_json_response():
        if sync_count > 0 and fail_count == 0:
            message = api_message('apparmor_policies_updated_synced', username=user.username)
        elif fail_count > 0:
            message = api_message('apparmor_policies_updated_sync_partial', username=user.username)
        else:
            message = api_message('apparmor_policies_updated_offline', username=user.username)
        return jsonify({
            'success': True,
            'message': message,
            'sync_count': sync_count,
            'fail_count': fail_count,
            'sync_pending': sync_pending,
        })

    if sync_count > 0 and fail_count == 0:
        flash_t('flash.apparmor.policies_updated_synced', 'success', username=user.username)
    elif fail_count > 0:
        flash_t('flash.apparmor.policies_updated_sync_partial', 'warning', username=user.username)
    else:
        flash_t('flash.apparmor.policies_updated_offline', 'success', username=user.username)

    return redirect(url_for('ui_dashboard.edit_user_profile', user_id=user.id))
