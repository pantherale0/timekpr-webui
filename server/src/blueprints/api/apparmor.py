import logging
from flask import Blueprint, session, request, redirect, url_for, flash, jsonify
from src.database import db, ManagedUserDeviceMap, AppArmorRule
from src.agent_helper import AgentConnectionManager, AgentClient
from src.helpers import _get_device_label_map
from src.apparmor_manager import (
    CURATED_APPARMOR_APPS,
    _is_valid_preset_for_match_type,
    _validate_apparmor_rule_target,
    _build_apparmor_policy_sync_payload,
)

_LOGGER = logging.getLogger(__name__)

api_apparmor_bp = Blueprint('api_apparmor', __name__)


@api_apparmor_bp.route('/apparmor/policy/<int:mapping_id>', methods=['POST'])
def save_apparmor_policy(mapping_id):
    """Visual AppArmor policy management for a single device mapping (POST)."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    mapping = ManagedUserDeviceMap.query.get_or_404(mapping_id)
    device_labels = _get_device_label_map()
    device_label = device_labels.get(mapping.system_id, mapping.system_id)

    # Process preset changes for curated apps
    for app_template in CURATED_APPARMOR_APPS:
        preset = request.form.get(f"preset_{app_template['path']}", 'allowed').strip()
        if not _is_valid_preset_for_match_type(AppArmorRule.MATCH_TYPE_EXECUTABLE, preset):
            preset = AppArmorRule.PRESET_ALLOWED

        existing = AppArmorRule.query.filter_by(
            device_map_id=mapping.id,
            executable_path=app_template['path'],
            match_type=AppArmorRule.MATCH_TYPE_EXECUTABLE,
        ).first()
        if existing:
            existing.preset = preset
            existing.application_name = app_template['name']
            existing.is_custom = False
            existing.match_type = AppArmorRule.MATCH_TYPE_EXECUTABLE
        else:
            db.session.add(AppArmorRule(
                device_map_id=mapping.id,
                application_name=app_template['name'],
                executable_path=app_template['path'],
                match_type=AppArmorRule.MATCH_TYPE_EXECUTABLE,
                preset=preset,
                is_custom=False,
            ))

    # Process custom app additions
    custom_name = (request.form.get('custom_app_name') or '').strip()
    custom_path = (request.form.get('custom_app_path') or '').strip()
    custom_match_type = (
        request.form.get('custom_app_match_type') or AppArmorRule.MATCH_TYPE_EXECUTABLE
    ).strip()
    custom_preset = (request.form.get('custom_app_preset') or 'allowed').strip()
    if custom_name and custom_path:
        if custom_match_type not in AppArmorRule.VALID_MATCH_TYPES:
            custom_match_type = AppArmorRule.MATCH_TYPE_EXECUTABLE
        try:
            custom_path = _validate_apparmor_rule_target(
                custom_match_type,
                custom_path,
                mapping.linux_username,
            )
        except ValueError as exc:
            flash(str(exc), 'danger')
            return redirect(url_for('ui_apparmor.apparmor_policy', mapping_id=mapping.id))
        if not _is_valid_preset_for_match_type(custom_match_type, custom_preset):
            custom_preset = AppArmorRule.PRESET_ALLOWED
        existing = AppArmorRule.query.filter_by(
            device_map_id=mapping.id,
            executable_path=custom_path,
            match_type=custom_match_type,
        ).first()
        if existing:
            existing.preset = custom_preset
            existing.application_name = custom_name
            existing.match_type = custom_match_type
        else:
            db.session.add(AppArmorRule(
                device_map_id=mapping.id,
                application_name=custom_name,
                executable_path=custom_path,
                match_type=custom_match_type,
                preset=custom_preset,
                is_custom=True,
            ))

    # Process custom rule presets from the existing list
    custom_rules = AppArmorRule.query.filter_by(
        device_map_id=mapping.id,
        is_custom=True,
    ).all()
    for rule in custom_rules:
        form_key = f"preset_rule_{rule.id}"
        if form_key in request.form:
            new_preset = request.form[form_key].strip()
            if _is_valid_preset_for_match_type(rule.match_type, new_preset):
                rule.preset = new_preset

    db.session.commit()

    # Push the policy to the agent if it is online
    policies_list, skipped_rule_names = _build_apparmor_policy_sync_payload(mapping)
    if skipped_rule_names:
        flash(
            'Skipped unsafe AppArmor rules during sync: ' + ', '.join(sorted(skipped_rule_names)),
            'warning',
        )
    if AgentConnectionManager.is_online(mapping.system_id):
        agent = AgentClient(system_id=mapping.system_id)
        success, sync_msg = agent.sync_apparmor_policy(
            mapping.linux_username,
            policies_list,
        )
        if success:
            flash(f'AppArmor policy saved and synced to {device_label}', 'success')
        else:
            flash(f'Policy saved but sync failed: {sync_msg}', 'warning')
    else:
        flash('Policy saved. Will sync when the device reconnects.', 'success')

    return redirect(url_for('ui_apparmor.apparmor_policy', mapping_id=mapping.id))


@api_apparmor_bp.route('/apparmor/rule/<int:rule_id>/delete', methods=['POST'])
def delete_apparmor_rule(rule_id):
    """Delete a custom AppArmor rule."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    rule = AppArmorRule.query.get_or_404(rule_id)
    mapping = ManagedUserDeviceMap.query.get_or_404(rule.device_map_id)
    mapping_id = rule.device_map_id
    rule_name = rule.application_name
    device_labels = _get_device_label_map()
    device_label = device_labels.get(mapping.system_id, mapping.system_id)
    db.session.delete(rule)
    db.session.commit()

    policies_list, skipped_rule_names = _build_apparmor_policy_sync_payload(mapping)
    if skipped_rule_names:
        flash(
            'Skipped unsafe AppArmor rules during sync: ' + ', '.join(sorted(skipped_rule_names)),
            'warning',
        )

    if AgentConnectionManager.is_online(mapping.system_id):
        agent = AgentClient(system_id=mapping.system_id)
        success, sync_msg = agent.sync_apparmor_policy(
            mapping.linux_username,
            policies_list,
        )
        if success:
            flash(f'Removed AppArmor rule for {rule_name} and synced {device_label}', 'success')
        else:
            flash(f'Removed AppArmor rule for {rule_name}, but sync failed: {sync_msg}', 'warning')
    else:
        flash(f'Removed AppArmor rule for {rule_name}. Will sync when the device reconnects.', 'success')
    return redirect(url_for('ui_apparmor.apparmor_policy', mapping_id=mapping_id))
