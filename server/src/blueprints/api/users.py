import json
import logging
from datetime import datetime, timezone
from flask import Blueprint, session, request, jsonify, flash, redirect, url_for
from src.database import db, ManagedUser, AgentDevice, ManagedUserDeviceMap
from src.helpers import _device_display_label, _get_device_label_map, _mapping_display_label
from src.users_manager import _refresh_managed_user_summary
from src.agent_helper import AgentClient

_LOGGER = logging.getLogger(__name__)

api_users_bp = Blueprint('api_users', __name__)


@api_users_bp.route('/managed-users/add', methods=['POST'])
def create_managed_user():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    username = (request.form.get('username') or '').strip()
    if not username:
        flash('Managed user name is required', 'danger')
        return redirect(url_for('ui_dashboard.admin'))

    existing_user = ManagedUser.query.filter_by(username=username).first()
    if existing_user:
        flash(f'Managed user {username} already exists', 'warning')
        return redirect(url_for('ui_dashboard.admin'))

    managed_user = ManagedUser(
        username=username,
        is_valid=False,
        system_ip='Unassigned',
    )
    db.session.add(managed_user)
    db.session.commit()

    flash(f'Managed user {username} created', 'success')
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/managed-users/<int:user_id>/mappings/add', methods=['POST'])
def add_user_mapping(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    system_id = (request.form.get('system_id') or '').strip()
    linux_username = (request.form.get('linux_username') or '').strip()
    linux_uid_raw = (request.form.get('linux_uid') or '').strip()

    if not system_id or not linux_username:
        flash('Device and Linux username are required', 'danger')
        return redirect(url_for('ui_dashboard.admin'))

    device = AgentDevice.query.get(system_id)
    if not device or device.status != 'approved':
        flash(f'Device {_device_display_label(system_id)} is not registered or approved', 'danger')
        return redirect(url_for('ui_dashboard.admin'))

    device_label = _device_display_label(system_id)
    existing_mapping = ManagedUserDeviceMap.query.filter_by(
        managed_user_id=user.id,
        system_id=system_id,
    ).first()
    if existing_mapping:
        flash(f'{user.username} is already linked to {device_label}', 'warning')
        return redirect(url_for('ui_dashboard.admin'))

    linux_uid = None
    if linux_uid_raw:
        try:
            linux_uid = int(linux_uid_raw)
        except ValueError:
            flash('Linux UID must be numeric', 'danger')
            return redirect(url_for('ui_dashboard.admin'))

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=system_id,
        linux_username=linux_username,
        linux_uid=linux_uid,
        is_valid=False,
    )
    db.session.add(mapping)
    db.session.commit()

    from app import task_manager
    task_manager.notify_domain_policy_hint(system_ids={system_id}, reason='mapping_updated')

    flash(f'Mapping added: {user.username} -> {linux_username}@{device_label}', 'success')
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/users/add', methods=['GET', 'POST'])
def add_user():
    """
    Backward-compatible endpoint that creates a managed user and one mapping.
    """
    if request.method == 'GET':
        return redirect(url_for('ui_dashboard.admin'))
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    username = (request.form.get('username') or '').strip()
    system_id = (request.form.get('system_id') or '').strip()

    if not username or not system_id:
        flash('Both username and device are required', 'danger')
        return redirect(url_for('ui_dashboard.admin'))

    device = AgentDevice.query.get(system_id)
    if not device or device.status != 'approved':
        flash(f'Device {_device_display_label(system_id)} is not registered or approved', 'danger')
        return redirect(url_for('ui_dashboard.admin'))

    device_label = _device_display_label(system_id)
    user = ManagedUser.query.filter_by(username=username).first()
    if not user:
        user = ManagedUser(username=username, is_valid=False, system_ip='Unassigned')
        db.session.add(user)
        db.session.flush()

    existing_mapping = ManagedUserDeviceMap.query.filter_by(
        managed_user_id=user.id,
        system_id=system_id,
    ).first()
    if existing_mapping:
        db.session.rollback()
        flash(f'User {username} on {device_label} already exists', 'warning')
        return redirect(url_for('ui_dashboard.admin'))

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=system_id,
        linux_username=username,
    )
    db.session.add(mapping)
    db.session.commit()

    from app import task_manager
    task_manager.notify_domain_policy_hint(system_ids={system_id}, reason='mapping_updated')
    flash(f'Managed user {username} and mapping added', 'success')
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/users/validate/<int:user_id>')
def validate_user(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    mappings = list(user.device_mappings)
    if not mappings:
        flash('No device mappings configured for this managed user', 'warning')
        return redirect(url_for('ui_dashboard.admin'))

    total_valid = 0
    messages = []
    device_labels = _get_device_label_map()
    policy_hint_system_ids = set()
    for mapping in mappings:
        previous_linux_uid = mapping.linux_uid
        agent_client = AgentClient(system_id=mapping.system_id)
        is_valid, message, config_dict = agent_client.validate_user(mapping.linux_username)
        mapping.last_checked = datetime.now(timezone.utc)
        mapping.is_valid = is_valid
        if is_valid and config_dict:
            mapping.last_config = json.dumps(config_dict)
            if config_dict.get("LINUX_UID") is not None:
                try:
                    mapping.linux_uid = int(config_dict.get("LINUX_UID"))
                except (TypeError, ValueError):
                    pass
            if mapping.linux_uid != previous_linux_uid:
                policy_hint_system_ids.add(mapping.system_id)
            total_valid += 1
        else:
            messages.append(f"{_mapping_display_label(mapping, device_labels)}: {message}")

    _refresh_managed_user_summary(user)

    db.session.commit()
    from src.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('mapping_changed')
    if policy_hint_system_ids:
        from app import task_manager
        task_manager.notify_domain_policy_hint(
            system_ids=policy_hint_system_ids,
            reason='mapping_updated',
        )
    if total_valid:
        flash(f'Validated {total_valid}/{len(mappings)} mapping(s) for {user.username}', 'success')
    else:
        flash(f'User validation failed: {"; ".join(messages) if messages else "No mappings validated"}', 'danger')
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/managed-users/<int:user_id>/mappings/<int:mapping_id>/validate')
def validate_mapping(user_id, mapping_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    mapping = ManagedUserDeviceMap.query.filter_by(id=mapping_id, managed_user_id=user.id).first_or_404()
    agent_client = AgentClient(system_id=mapping.system_id)
    is_valid, message, config_dict = agent_client.validate_user(mapping.linux_username)

    previous_linux_uid = mapping.linux_uid
    mapping.last_checked = datetime.now(timezone.utc)
    mapping.is_valid = is_valid
    if is_valid and config_dict:
        mapping.last_config = json.dumps(config_dict)
        if config_dict.get("LINUX_UID") is not None:
            try:
                mapping.linux_uid = int(config_dict.get("LINUX_UID"))
            except (TypeError, ValueError):
                pass

    _refresh_managed_user_summary(user)
    db.session.commit()
    from src.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('mapping_changed')
    if mapping.linux_uid != previous_linux_uid:
        from app import task_manager
        task_manager.notify_domain_policy_hint(
            system_ids={mapping.system_id},
            reason='mapping_updated',
        )
    device_labels = _get_device_label_map()

    if is_valid:
        flash(f'Mapping validated: {_mapping_display_label(mapping, device_labels)}', 'success')
    else:
        flash(f'Mapping validation failed: {message}', 'danger')
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/managed-users/<int:user_id>/mappings/<int:mapping_id>/delete', methods=['POST'])
def delete_mapping(user_id, mapping_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    mapping = ManagedUserDeviceMap.query.filter_by(id=mapping_id, managed_user_id=user.id).first_or_404()
    mapping_label = _mapping_display_label(mapping)
    affected_system_id = mapping.system_id
    db.session.delete(mapping)
    db.session.flush()
    _refresh_managed_user_summary(user)
    db.session.commit()
    from src.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('mapping_changed')

    from app import task_manager
    task_manager.notify_domain_policy_hint(system_ids={affected_system_id}, reason='mapping_updated')
    flash(f'Mapping removed: {mapping_label}', 'success')
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/users/delete/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    username = user.username
    affected_system_ids = {mapping.system_id for mapping in user.device_mappings}
    
    db.session.delete(user)
    db.session.commit()
    if affected_system_ids:
        from app import task_manager
        task_manager.notify_domain_policy_hint(system_ids=affected_system_ids, reason='mapping_updated')
    
    flash(f'User {username} removed successfully', 'success')
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/api/user/<int:user_id>/usage')
def get_user_usage(user_id):
    """API endpoint to get user usage data"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    days = request.args.get('days', 7, type=int)
    
    usage_data = user.get_recent_usage(days=days)
    
    # Format for chart.js
    labels = list(usage_data.keys())
    values = list(usage_data.values())
    
    # Convert seconds to hours for better readability
    values_hours = [round(v / 3600, 1) for v in values]
    
    return jsonify({
        'success': True,
        'labels': labels,
        'values': values_hours,
        'username': user.username
    })


@api_users_bp.route('/api/users', methods=['GET'])
def get_all_users():
    """Return all child profiles in JSON format for the onboarding wizard."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    users = ManagedUser.query.order_by(ManagedUser.username.asc()).all()
    return jsonify({
        'success': True,
        'users': [{'id': u.id, 'username': u.username} for u in users]
    })


@api_users_bp.route('/api/user/create', methods=['POST'])
def api_create_user():
    """Create a new child profile and return its JSON details for the wizard."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    if request.is_json:
        data = request.json or {}
        username = (data.get('username') or '').strip()
    else:
        username = (request.form.get('username') or '').strip()
        
    if not username:
        return jsonify({'success': False, 'message': 'Profile name is required'}), 400
        
    existing = ManagedUser.query.filter_by(username=username).first()
    if existing:
        return jsonify({'success': False, 'message': f'Child profile "{username}" already exists'}), 400
        
    user = ManagedUser(username=username, is_valid=False, system_ip='Unassigned')
    db.session.add(user)
    db.session.commit()
    return jsonify({
        'success': True,
        'user': {'id': user.id, 'username': user.username}
    })


