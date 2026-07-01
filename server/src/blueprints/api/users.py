import json
import logging
from datetime import datetime, timezone, timedelta, time
from flask import Blueprint, session, request, jsonify, redirect, url_for
from src.i18n.catalog import flash_t, api_message, t
from src.models import (
    db,
    ManagedUser,
    AgentDevice,
    ManagedUserDeviceMap,
    UserTimeUsage,
    AppUsageHistory,
    stamp_usage_snapshot,
    utc_today,
)
from src.common.helpers import _device_display_label, _get_device_label_map, _mapping_display_label
from src.user.manager import _refresh_managed_user_summary
from src.agent.helper import AgentClient
from src.device.installed_apps import list_installed_apps_for_managed_user

_LOGGER = logging.getLogger(__name__)

_VALID_AGE_TIERS = {"under8", "eight12", "teen"}

api_users_bp = Blueprint('api_users', __name__)


def _apply_mapping_validation(mapping):
    """Validate mapping against the agent; update mapping fields in place."""
    previous_linux_uid = mapping.linux_uid
    agent_client = AgentClient(system_id=mapping.system_id)
    is_valid, message, config_dict = agent_client.validate_user(
        mapping.linux_username,
        linux_uid=mapping.linux_uid,
    )
    mapping.last_checked = datetime.now(timezone.utc)
    mapping.is_valid = is_valid
    if is_valid and config_dict:
        mapping.last_config = json.dumps(
            stamp_usage_snapshot(config_dict, utc_today())
        )
        if config_dict.get("LINUX_UID") is not None:
            try:
                mapping.linux_uid = int(config_dict.get("LINUX_UID"))
            except (TypeError, ValueError):
                pass
    uid_changed = mapping.linux_uid != previous_linux_uid
    return is_valid, message, uid_changed


def _parse_linux_uid(raw_value):
    if raw_value is None:
        return None
    if isinstance(raw_value, int):
        return raw_value
    text = str(raw_value).strip()
    if not text:
        return None
    return int(text)


@api_users_bp.route('/managed-users/add', methods=['POST'])
def create_managed_user():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    username = (request.form.get('username') or '').strip()
    household_id = request.form.get('household_id')
    selected_preset_ids = request.form.getlist('preset_ids')
    policy_age_bracket = (request.form.get('policy_age_bracket') or '').strip()
    policy_maturity_level = (request.form.get('policy_maturity_level') or '').strip()

    if not username:
        flash_t('flash.users.name_required', 'danger')
        return redirect(url_for('ui_dashboard.admin'))

    existing_user = ManagedUser.query.filter_by(username=username).first()
    if existing_user:
        flash_t('flash.users.already_exists', 'warning', username=username)
        return redirect(url_for('ui_dashboard.admin'))

    parent_id = session.get('parent_account_id')
    if not parent_id:
        from src.models import ParentAccount
        p = ParentAccount.query.filter_by(email='admin@local').first()
        if p:
            parent_id = p.id

    target_hh_id = None
    if household_id:
        try:
            target_hh_id = int(household_id)
        except (ValueError, TypeError):
            pass

    if not target_hh_id and parent_id:
        from src.models import ParentAccount
        p = ParentAccount.query.get(parent_id)
        if p and p.memberships:
            target_hh_id = p.memberships[0].household_id

    managed_user = ManagedUser(
        username=username,
        is_valid=False,
        system_ip='Unassigned',
        household_id=target_hh_id,
    )
    db.session.add(managed_user)
    db.session.commit()

    if policy_age_bracket and policy_maturity_level:
        from src.policy.presets import apply_policy_preset
        try:
            apply_policy_preset(managed_user, policy_age_bracket, policy_maturity_level)
        except ValueError as exc:
            _LOGGER.error(
                "Failed to apply policy preset for user %s: %s",
                username,
                exc,
            )
            flash_t('flash.users.preset_partial', 'warning', error=str(exc))
        except Exception as exc:
            _LOGGER.error(
                "Failed to apply policy preset for user %s: %s",
                username,
                exc,
            )
            flash_t('flash.users.preset_not_applied', 'warning')
    elif selected_preset_ids:
        from src.blocklist.marketplace import sync_marketplace_subscriptions
        try:
            sync_marketplace_subscriptions(managed_user, selected_preset_ids)
        except Exception as exc:
            _LOGGER.error("Failed to subscribe user %s to marketplace presets: %s", username, exc)

    # Advanced override: individual filter packs after composite preset
    if selected_preset_ids and policy_age_bracket and policy_maturity_level:
        from src.blocklist.marketplace import sync_marketplace_subscriptions
        try:
            sync_marketplace_subscriptions(managed_user, selected_preset_ids)
        except Exception as exc:
            _LOGGER.error(
                "Failed to apply advanced marketplace overrides for %s: %s",
                username,
                exc,
            )

    flash_t('flash.users.created', 'success', username=username)
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/managed-users/<int:user_id>/apply-policy-preset', methods=['POST'])
def apply_policy_preset_route(user_id):
    """Apply or re-apply an age × maturity policy preset to a managed child profile."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    user = ManagedUser.query.get_or_404(user_id)
    policy_age_bracket = (request.form.get('policy_age_bracket') or '').strip()
    policy_maturity_level = (request.form.get('policy_maturity_level') or '').strip()

    if not policy_age_bracket or not policy_maturity_level:
        flash_t('flash.users.preset_fields_required', 'danger')
        return redirect(url_for('ui_dashboard.edit_user_profile', user_id=user.id))

    from src.policy.presets import apply_policy_preset

    try:
        apply_policy_preset(user, policy_age_bracket, policy_maturity_level)
        flash_t('flash.users.preset_applied', 'success', username=user.username)
    except ValueError as exc:
        flash_t('flash.users.preset_failed', 'danger', error=str(exc))
    except Exception as exc:
        _LOGGER.error(
            "Failed to apply policy preset for user %s (id=%d): %s",
            user.username,
            user.id,
            exc,
        )
        flash_t('flash.users.preset_apply_failed', 'danger')

    return redirect(url_for('ui_dashboard.edit_user_profile', user_id=user.id))


@api_users_bp.route('/managed-users/<int:user_id>/mappings/add', methods=['POST'])
def add_user_mapping(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    user = ManagedUser.query.get_or_404(user_id)
    system_id = (request.form.get('system_id') or '').strip()
    linux_username = (request.form.get('linux_username') or '').strip()
    linux_uid_raw = (request.form.get('linux_uid') or '').strip()

    if not system_id or not linux_username:
        flash_t('flash.users.mapping_fields_required', 'danger')
        return redirect(url_for('ui_dashboard.admin'))

    device = AgentDevice.query.get(system_id)
    if not device or device.status != 'approved':
        flash_t(
            'flash.users.device_not_registered',
            'danger',
            device=_device_display_label(system_id),
        )
        return redirect(url_for('ui_dashboard.admin'))

    device_label = _device_display_label(system_id)
    existing_mapping = ManagedUserDeviceMap.query.filter_by(
        managed_user_id=user.id,
        system_id=system_id,
    ).first()
    if existing_mapping:
        flash_t(
            'flash.users.already_linked',
            'warning',
            username=user.username,
            device=device_label,
        )
        return redirect(url_for('ui_dashboard.admin'))

    linux_uid = None
    if linux_uid_raw:
        try:
            linux_uid = int(linux_uid_raw)
        except ValueError:
            flash_t('flash.users.uid_numeric', 'danger')
            return redirect(url_for('ui_dashboard.admin'))

    android_profile_type = request.form.get('android_profile_type')
    if android_profile_type not in ('restricted', 'standard'):
        android_profile_type = None

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=system_id,
        linux_username=linux_username,
        linux_uid=linux_uid,
        is_valid=False,
        android_profile_type=android_profile_type,
    )
    db.session.add(mapping)
    db.session.commit()

    from app import task_manager
    task_manager.notify_domain_policy_hint(user.household_id, system_ids={system_id}, reason='mapping_updated')

    flash_t(
        'flash.users.mapping_added',
        'success',
        username=user.username,
        linux_username=linux_username,
        device=device_label,
    )
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/api/managed-users/<int:user_id>/mappings/connect', methods=['POST'])
def connect_user_mapping(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    payload = request.get_json(silent=True) or {}
    system_id = (payload.get('system_id') or '').strip()
    linux_username = (payload.get('linux_username') or '').strip()
    linux_uid_raw = payload.get('linux_uid')

    if not system_id or not linux_username:
        return jsonify({
            'success': False,
            'message': api_message('mapping_fields_required'),
        }), 400

    user = ManagedUser.query.get_or_404(user_id)
    device = AgentDevice.query.get(system_id)
    if not device or device.status != 'approved':
        return jsonify({
            'success': False,
            'message': api_message('device_not_registered', device=_device_display_label(system_id)),
        }), 400

    device_labels = _get_device_label_map()
    device_label = device_labels.get(system_id, _device_display_label(system_id))
    existing_mapping = ManagedUserDeviceMap.query.filter_by(
        managed_user_id=user.id,
        system_id=system_id,
    ).first()
    if existing_mapping:
        return jsonify({
            'success': False,
            'message': api_message('already_linked', username=user.username, device=device_label),
        }), 409

    try:
        linux_uid = _parse_linux_uid(linux_uid_raw)
    except ValueError:
        return jsonify({'success': False, 'message': api_message('uid_numeric')}), 400

    android_profile_type = payload.get('android_profile_type')
    if android_profile_type not in ('restricted', 'standard'):
        android_profile_type = None

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=system_id,
        linux_username=linux_username,
        linux_uid=linux_uid,
        is_valid=False,
        android_profile_type=android_profile_type,
    )
    db.session.add(mapping)
    db.session.flush()

    is_valid, validation_message, uid_changed = _apply_mapping_validation(mapping)
    _refresh_managed_user_summary(user)
    db.session.commit()

    from src.common.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('mapping_changed')

    policy_hint_system_ids = set()
    if uid_changed:
        policy_hint_system_ids.add(system_id)
    from app import task_manager
    task_manager.notify_domain_policy_hint(user.household_id, system_ids={system_id}, reason='mapping_updated')
    if policy_hint_system_ids:
        task_manager.notify_domain_policy_hint(
            user.household_id,
            system_ids=policy_hint_system_ids,
            reason='mapping_updated',
        )

    display_label = _mapping_display_label(mapping, device_labels)
    return jsonify({
        'success': True,
        'message': api_message(
            'mapping_connected',
            username=user.username,
            device=device_label,
        ),
        'mapping': {
            'id': mapping.id,
            'is_valid': is_valid,
            'linux_uid': mapping.linux_uid,
            'display_label': display_label,
        },
        'validation_message': validation_message if not is_valid else None,
    })


@api_users_bp.route('/users/add', methods=['GET', 'POST'])
def add_user():
    """
    Backward-compatible endpoint that creates a managed user and one mapping.
    """
    if request.method == 'GET':
        return redirect(url_for('ui_dashboard.admin'))
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    username = (request.form.get('username') or '').strip()
    system_id = (request.form.get('system_id') or '').strip()
    android_profile_type = request.form.get('android_profile_type')
    if android_profile_type not in ('restricted', 'standard'):
        android_profile_type = None

    if not username or not system_id:
        flash_t('flash.users.mapping_fields_both', 'danger')
        return redirect(url_for('ui_dashboard.admin'))

    device = AgentDevice.query.get(system_id)
    if not device or device.status != 'approved':
        flash_t(
            'flash.users.device_not_registered',
            'danger',
            device=_device_display_label(system_id),
        )
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
        flash_t(
            'flash.users.user_device_exists',
            'warning',
            username=username,
            device=device_label,
        )
        return redirect(url_for('ui_dashboard.admin'))

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=system_id,
        linux_username=username,
        android_profile_type=android_profile_type,
    )
    db.session.add(mapping)
    db.session.commit()

    selected_preset_ids = request.form.getlist('preset_ids')
    if selected_preset_ids:
        from src.blocklist.marketplace import sync_marketplace_subscriptions
        try:
            sync_marketplace_subscriptions(user, selected_preset_ids)
        except Exception as exc:
            _LOGGER.error("Failed to subscribe user %s to marketplace presets: %s", username, exc)

    from app import task_manager
    task_manager.notify_domain_policy_hint(user.household_id, system_ids={system_id}, reason='mapping_updated')
    flash_t('flash.users.created_with_mapping', 'success', username=username)
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/users/validate/<int:user_id>')
def validate_user(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    user = ManagedUser.query.get_or_404(user_id)
    mappings = list(user.device_mappings)
    if not mappings:
        flash_t('flash.users.no_mappings', 'warning')
        return redirect(url_for('ui_dashboard.admin'))

    total_valid = 0
    messages = []
    device_labels = _get_device_label_map()
    policy_hint_system_ids = set()
    for mapping in mappings:
        previous_linux_uid = mapping.linux_uid
        is_valid, message, uid_changed = _apply_mapping_validation(mapping)
        if uid_changed:
            policy_hint_system_ids.add(mapping.system_id)
        if is_valid:
            total_valid += 1
        else:
            messages.append(f"{_mapping_display_label(mapping, device_labels)}: {message}")

    _refresh_managed_user_summary(user)

    db.session.commit()
    from src.common.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('mapping_changed')
    if policy_hint_system_ids:
        from app import task_manager
        task_manager.notify_domain_policy_hint(
            user.household_id,
            system_ids=policy_hint_system_ids,
            reason='mapping_updated',
        )
    if total_valid:
        flash_t(
            'flash.users.mappings_validated',
            'success',
            valid=total_valid,
            total=len(mappings),
            username=user.username,
        )
    else:
        details = '; '.join(messages) if messages else t('flash.users.no_mappings_validated')
        flash_t('flash.users.validation_failed', 'danger', details=details)
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/managed-users/<int:user_id>/mappings/<int:mapping_id>/validate')
def validate_mapping(user_id, mapping_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    user = ManagedUser.query.get_or_404(user_id)
    mapping = ManagedUserDeviceMap.query.filter_by(id=mapping_id, managed_user_id=user.id).first_or_404()
    previous_linux_uid = mapping.linux_uid
    is_valid, message, uid_changed = _apply_mapping_validation(mapping)

    _refresh_managed_user_summary(user)
    db.session.commit()
    from src.common.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('mapping_changed')
    if uid_changed or mapping.linux_uid != previous_linux_uid:
        from app import task_manager
        task_manager.notify_domain_policy_hint(
            user.household_id,
            system_ids={mapping.system_id},
            reason='mapping_updated',
        )
    device_labels = _get_device_label_map()

    if is_valid:
        flash_t(
            'flash.users.mapping_validated',
            'success',
            label=_mapping_display_label(mapping, device_labels),
        )
    else:
        flash_t('flash.users.mapping_validation_failed', 'danger', message=message)
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/managed-users/<int:user_id>/mappings/<int:mapping_id>/delete', methods=['POST'])
def delete_mapping(user_id, mapping_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    user = ManagedUser.query.get_or_404(user_id)
    mapping = ManagedUserDeviceMap.query.filter_by(id=mapping_id, managed_user_id=user.id).first_or_404()
    mapping_label = _mapping_display_label(mapping)
    affected_system_id = mapping.system_id
    db.session.delete(mapping)
    db.session.flush()
    _refresh_managed_user_summary(user)
    db.session.commit()
    from src.common.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('mapping_changed')

    from app import task_manager
    task_manager.notify_domain_policy_hint(user.household_id, system_ids={affected_system_id}, reason='mapping_updated')
    flash_t('flash.users.mapping_removed', 'success', label=mapping_label)
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/users/delete/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    username = user.username
    affected_system_ids = {mapping.system_id for mapping in user.device_mappings}
    
    db.session.delete(user)
    db.session.commit()
    if affected_system_ids:
        from app import task_manager
        task_manager.notify_domain_policy_hint(user.household_id, system_ids=affected_system_ids, reason='mapping_updated')
    
    flash_t('flash.users.user_removed', 'success', username=username)
    return redirect(url_for('ui_dashboard.admin'))


@api_users_bp.route('/api/user/<int:user_id>/usage')
def get_user_usage(user_id):
    """API endpoint to get user usage data"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
    
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
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
    
    parent_id = session.get('parent_account_id')
    if not parent_id:
        from src.models import ParentAccount
        p = ParentAccount.query.filter_by(email='admin@local').first()
        if p:
            parent_id = p.id

    query = ManagedUser.query
    if parent_id:
        from src.common.helpers import get_accessible_child_ids
        allowed_ids = get_accessible_child_ids(parent_id)
        query = query.filter(ManagedUser.id.in_(allowed_ids))

    users = query.order_by(ManagedUser.username.asc()).all()
    return jsonify({
        'success': True,
        'users': [{'id': u.id, 'username': u.username} for u in users]
    })


@api_users_bp.route('/api/user/create', methods=['POST'])
def api_create_user():
    """Create a new child profile and return its JSON details for the wizard."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
    
    household_id = None
    if request.is_json:
        data = request.json or {}
        username = (data.get('username') or '').strip()
        household_id = data.get('household_id')
    else:
        username = (request.form.get('username') or '').strip()
        household_id = request.form.get('household_id')
        
    if not username:
        return jsonify({'success': False, 'message': api_message('profile_name_required')}), 400
        
    existing = ManagedUser.query.filter_by(username=username).first()
    if existing:
        return jsonify({
            'success': False,
            'message': api_message('profile_exists', username=username),
        }), 400
        
    parent_id = session.get('parent_account_id')
    if not parent_id:
        from src.models import ParentAccount
        p = ParentAccount.query.filter_by(email='admin@local').first()
        if p:
            parent_id = p.id

    target_hh_id = None
    if household_id:
        try:
            target_hh_id = int(household_id)
        except (ValueError, TypeError):
            pass

    if not target_hh_id and parent_id:
        from src.models import ParentAccount
        p = ParentAccount.query.get(parent_id)
        if p and p.memberships:
            target_hh_id = p.memberships[0].household_id

    user = ManagedUser(username=username, is_valid=False, system_ip='Unassigned', household_id=target_hh_id)
    db.session.add(user)
    db.session.commit()
    return jsonify({
        'success': True,
        'user': {'id': user.id, 'username': user.username}
    })


@api_users_bp.route('/api/user/<int:user_id>/stats')
def get_user_stats(user_id):
    """API endpoint to get user usage analytics, including daily totals and per-app usage in a date range."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    user = ManagedUser.query.get_or_404(user_id)
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    today = datetime.now(timezone.utc).date()
    try:
        if start_date_str:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        else:
            start_date = today - timedelta(days=29)

        if end_date_str:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        else:
            end_date = today
    except ValueError:
        return jsonify({'success': False, 'message': api_message('invalid_date')}), 400

    # 1. Query overall system/device usage
    records = UserTimeUsage.query.filter_by(user_id=user.id).filter(
        UserTimeUsage.date >= start_date,
        UserTimeUsage.date <= end_date
    ).order_by(UserTimeUsage.date).all()

    num_days = (end_date - start_date).days + 1
    daily_usage = {}
    for i in range(num_days):
        d = start_date + timedelta(days=i)
        daily_usage[d.strftime('%Y-%m-%d')] = 0

    for r in records:
        daily_usage[r.date.strftime('%Y-%m-%d')] = r.time_spent

    total_seconds = sum(r.time_spent for r in records)
    daily_average_seconds = total_seconds / max(1, num_days)
    peak_record = max(records, key=lambda r: r.time_spent) if records else None
    peak_seconds = peak_record.time_spent if peak_record else 0
    peak_date = peak_record.date.strftime('%Y-%m-%d') if peak_record else '—'

    # 2. Query per-application usage
    mapping_ids = [m.id for m in user.device_mappings]
    app_list = []
    if mapping_ids:
        start_dt = datetime.combine(start_date, time.min).replace(tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date, time.max).replace(tzinfo=timezone.utc)

        app_records = AppUsageHistory.query.filter(
            AppUsageHistory.device_map_id.in_(mapping_ids),
            AppUsageHistory.start_time >= start_dt,
            AppUsageHistory.start_time <= end_dt
        ).all()

        app_aggregates = {}
        for r in app_records:
            key = r.executable_path or r.application_name
            entry = app_aggregates.setdefault(key, {
                'application_name': r.application_name,
                'executable_path': r.executable_path,
                'total_seconds': 0,
                'session_count': 0,
            })
            entry['total_seconds'] += r.duration_seconds
            entry['session_count'] += 1

        # Retrieve app metadata (icons, platform)
        installed_apps = list_installed_apps_for_managed_user(user.id, present_only=False)
        app_meta = {}
        for app in installed_apps:
            identifier = app.get('identifier')
            if identifier:
                app_meta[identifier] = {
                    'icon_hash': app.get('icon_hash'),
                    'platform': app.get('platform'),
                }

        for key, data in app_aggregates.items():
            meta = app_meta.get(data['executable_path'], {})
            app_list.append({
                'application_name': data['application_name'],
                'executable_path': data['executable_path'],
                'total_seconds': data['total_seconds'],
                'session_count': data['session_count'],
                'icon_hash': meta.get('icon_hash'),
                'platform': meta.get('platform') or 'linux',
            })
        app_list.sort(key=lambda x: -x['total_seconds'])

    return jsonify({
        'success': True,
        'username': user.username,
        'summary': {
            'total_seconds': total_seconds,
            'daily_average_seconds': daily_average_seconds,
            'peak_seconds': peak_seconds,
            'peak_date': peak_date,
        },
        'daily_usage': daily_usage,
        'app_usage': app_list,
    })


@api_users_bp.route('/managed-users/<int:user_id>/overlay', methods=['PATCH'])
def update_overlay_settings(user_id):
    """Update the Guardian Space overlay configuration for a managed user.

    Accepts JSON body with optional ``overlay_age_tier`` and ``overlay_parent_note`` fields.
    """
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    user = ManagedUser.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}

    if 'overlay_age_tier' in data:
        age_tier = (data['overlay_age_tier'] or '').strip() or None
        if age_tier is not None and age_tier not in _VALID_AGE_TIERS:
            return jsonify({
                'success': False,
                'message': api_message(
                    'overlay_age_tier_invalid',
                    tiers=', '.join(sorted(_VALID_AGE_TIERS)),
                ),
            }), 400
        user.overlay_age_tier = age_tier

    if 'overlay_parent_note' in data:
        note = data['overlay_parent_note']
        if note is not None:
            note = str(note).strip() or None
        user.overlay_parent_note = note

    db.session.commit()
    _LOGGER.info("Updated overlay settings for managed user %s (id=%d)", user.username, user.id)

    return jsonify({
        'success': True,
        'overlay_age_tier': user.overlay_age_tier,
        'overlay_parent_note': user.overlay_parent_note,
    })


@api_users_bp.route('/api/user/<int:user_id>/inspect', methods=['GET'])
def inspect_item(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    inspect_type = request.args.get('type', '').strip().lower()
    value = request.args.get('value', '').strip()

    if not inspect_type or not value:
        return jsonify({'success': False, 'message': 'Type and value are required'}), 400

    from src.models import WebHistory, AppUsageHistory, BlocklistSource, PolicyApprovalGrant, BlocklistDomain, AppArmorRule
    from sqlalchemy import func

    devices_distribution = {}
    total_visits = 0
    first_seen = None
    last_seen = None
    whitelisted = False

    device_map_ids = [m.id for m in user.device_mappings]

    if inspect_type == 'domain':
        records = WebHistory.query.filter_by(managed_user_id=user.id, domain=value).all()
        total_visits = len(records)
        if records:
            sorted_records = sorted(records, key=lambda r: r.visited_at)
            first_seen = sorted_records[0].visited_at.isoformat()
            last_seen = sorted_records[-1].visited_at.isoformat()
            for r in records:
                dev_label = r.device.system_hostname if r.device else r.device_id
                devices_distribution[dev_label] = devices_distribution.get(dev_label, 0) + 1

        if device_map_ids:
            grant = PolicyApprovalGrant.query.filter(
                PolicyApprovalGrant.device_map_id.in_(device_map_ids),
                PolicyApprovalGrant.grant_type == PolicyApprovalGrant.GRANT_DOMAIN_ACCESS,
                PolicyApprovalGrant.target_value == value,
                PolicyApprovalGrant.status == PolicyApprovalGrant.STATUS_ACTIVE
            ).first()
            if grant:
                whitelisted = True

        assigned_sources = [a.source for a in user.blocklist_assignments if a.source]
        active_shields = []
        for src in assigned_sources:
            in_src = BlocklistDomain.query.filter_by(source_id=src.id, domain=value).first()
            if in_src:
                active_shields.append({
                    'id': src.id,
                    'name': src.name,
                    'is_marketplace': src.is_marketplace
                })

        return jsonify({
            'success': True,
            'type': 'domain',
            'value': value,
            'total_visits': total_visits,
            'first_seen': first_seen,
            'last_seen': last_seen,
            'device_distribution': devices_distribution,
            'whitelisted': whitelisted,
            'active_shields': active_shields
        })

    elif inspect_type == 'app':
        records = AppUsageHistory.query.join(ManagedUserDeviceMap).filter(
            ManagedUserDeviceMap.managed_user_id == user.id,
            (AppUsageHistory.application_name == value) | (AppUsageHistory.executable_path == value)
        ).all()
        
        total_visits = len(records)
        total_duration = sum(r.duration_seconds for r in records)
        if records:
            sorted_records = sorted(records, key=lambda r: r.start_time)
            first_seen = sorted_records[0].start_time.isoformat()
            last_seen = sorted_records[-1].end_time.isoformat()
            for r in records:
                dev_label = r.device_map.device.system_hostname if (r.device_map and r.device_map.device) else r.device_map.system_id
                devices_distribution[dev_label] = devices_distribution.get(dev_label, 0) + 1

        if device_map_ids:
            grant = PolicyApprovalGrant.query.filter(
                PolicyApprovalGrant.device_map_id.in_(device_map_ids),
                PolicyApprovalGrant.grant_type == PolicyApprovalGrant.GRANT_APP_LAUNCH,
                PolicyApprovalGrant.target_value == value,
                PolicyApprovalGrant.status == PolicyApprovalGrant.STATUS_ACTIVE
            ).first()
            if grant:
                whitelisted = True

        active_rules = []
        if device_map_ids:
            rules = AppArmorRule.query.filter(
                AppArmorRule.device_map_id.in_(device_map_ids),
                AppArmorRule.executable_path == value
            ).all()
            for rule in rules:
                active_rules.append({
                    'device': rule.device_map.device.system_hostname if rule.device_map.device else rule.device_map.system_id,
                    'preset': rule.preset
                })

        return jsonify({
            'success': True,
            'type': 'app',
            'value': value,
            'total_launches': total_visits,
            'total_duration': total_duration,
            'first_seen': first_seen,
            'last_seen': last_seen,
            'device_distribution': devices_distribution,
            'whitelisted': whitelisted,
            'active_rules': active_rules
        })

    return jsonify({'success': False, 'message': 'Unsupported inspect type'}), 400


@api_users_bp.route('/api/user/<int:user_id>/whitelist', methods=['POST'])
def whitelist_item(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    inspect_type = request.form.get('type', '').strip().lower()
    value = request.form.get('value', '').strip()

    if not inspect_type or not value:
        return jsonify({'success': False, 'message': 'Type and value are required'}), 400

    from src.user.approvals import create_grant, get_session_actor
    from src.models import PolicyApprovalGrant, ApprovalRequest

    actor = get_session_actor() or 'admin'
    
    if not user.device_mappings:
        return jsonify({'success': False, 'message': 'No devices mapped to this child profile.'}), 400

    success_count = 0
    for mapping in user.device_mappings:
        if inspect_type == 'domain':
            create_grant(
                mapping=mapping,
                grant_type=PolicyApprovalGrant.GRANT_DOMAIN_ACCESS,
                target_kind=ApprovalRequest.TARGET_DOMAIN,
                target_value=value,
                display_label=value,
                created_by=actor
            )
            success_count += 1
        elif inspect_type == 'app':
            create_grant(
                mapping=mapping,
                grant_type=PolicyApprovalGrant.GRANT_APP_LAUNCH,
                target_kind=None,
                target_value=value,
                display_label=value,
                created_by=actor
            )
            success_count += 1

    return jsonify({
        'success': True,
        'message': f'Successfully whitelisted {value} across {success_count} device accounts.'
    })


@api_users_bp.route('/api/user/<int:user_id>/block', methods=['POST'])
def block_item(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    inspect_type = request.form.get('type', '').strip().lower()
    value = request.form.get('value', '').strip()
    source_id_raw = request.form.get('source_id', '').strip()
    new_source_name = request.form.get('new_source_name', '').strip()

    if not inspect_type or not value:
        return jsonify({'success': False, 'message': 'Type and value are required'}), 400

    if inspect_type == 'domain':
        from src.models import BlocklistSource, BlocklistDomain, ManagedUserBlocklistAssignment
        from src.blocklist.helper import normalize_domain, compute_source_revision

        try:
            domain = normalize_domain(value)
        except ValueError as exc:
            return jsonify({'success': False, 'message': str(exc)}), 400

        source = None
        if source_id_raw.isdigit():
            source = BlocklistSource.query.get(int(source_id_raw))
        elif new_source_name:
            source = BlocklistSource.query.filter_by(name=new_source_name).first()
            if not source:
                source = BlocklistSource(
                    name=new_source_name,
                    source_type=BlocklistSource.TYPE_MANUAL,
                    is_enabled=True,
                    content_revision=compute_source_revision([])
                )
                db.session.add(source)
                db.session.flush()

        if not source:
            return jsonify({'success': False, 'message': 'Invalid blocklist selection'}), 400

        if source.source_type != BlocklistSource.TYPE_MANUAL:
            return jsonify({'success': False, 'message': 'Can only add domains to manual shield lists.'}), 400

        existing_domain = BlocklistDomain.query.filter_by(source_id=source.id, domain=domain).first()
        if not existing_domain:
            db.session.add(BlocklistDomain(source_id=source.id, domain=domain))
            db.session.commit()
            
            domains_list = [row.domain for row in BlocklistDomain.query.with_entities(BlocklistDomain.domain).filter_by(source_id=source.id).all()]
            source.content_revision = compute_source_revision(domains_list)
            source.updated_at = datetime.now(timezone.utc)

        assignment = ManagedUserBlocklistAssignment.query.filter_by(managed_user_id=user.id, source_id=source.id).first()
        if not assignment:
            db.session.add(ManagedUserBlocklistAssignment(managed_user_id=user.id, source_id=source.id))

        db.session.commit()

        from app import task_manager
        task_manager.notify_domain_policy_hint(user.household_id, reason='blocklist_assignment_updated')

        return jsonify({
            'success': True,
            'message': f'Successfully blocked {domain} and assigned/updated shield list "{source.name}".'
        })

    elif inspect_type == 'app':
        from src.models import AppPolicy, AppPolicyRule, ManagedUserAppPolicyAssignment
        from src.policy.apparmor import compile_user_apparmor_rules
        from src.blueprints.ui.apparmor import _sync_mapping_app_policy_to_agent

        policy_name = f"Custom App Rules for {user.username}"
        policy = AppPolicy.query.filter_by(name=policy_name).first()
        if not policy:
            policy = AppPolicy(name=policy_name, platform=AppPolicy.PLATFORM_LINUX)
            db.session.add(policy)
            db.session.flush()

        assignment = ManagedUserAppPolicyAssignment.query.filter_by(managed_user_id=user.id, policy_id=policy.id).first()
        if not assignment:
            db.session.add(ManagedUserAppPolicyAssignment(managed_user_id=user.id, policy_id=policy.id))

        rule = AppPolicyRule.query.filter_by(policy_id=policy.id, executable_path=value).first()
        if rule:
            rule.preset = AppPolicyRule.PRESET_BLOCKED
        else:
            rule = AppPolicyRule(
                policy_id=policy.id,
                application_name=value.split('/')[-1] or value,
                executable_path=value,
                match_type=AppPolicyRule.MATCH_TYPE_EXECUTABLE,
                preset=AppPolicyRule.PRESET_BLOCKED,
                is_custom=True
            )
            db.session.add(rule)

        db.session.commit()

        compile_user_apparmor_rules(user)
        for mapping in user.device_mappings:
            _sync_mapping_app_policy_to_agent(mapping)

        return jsonify({
            'success': True,
            'message': f'Successfully blocked application {value} and updated custom rules.'
        })

    return jsonify({'success': False, 'message': 'Unsupported inspect type'}), 400


@api_users_bp.route('/api/blocklists/sources/manual', methods=['GET'])
def get_manual_blocklists():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    from src.models import BlocklistSource
    sources = BlocklistSource.query.filter_by(source_type=BlocklistSource.TYPE_MANUAL).all()
    return jsonify({
        'success': True,
        'sources': [{
            'id': src.id,
            'name': src.name,
            'is_marketplace': src.is_marketplace
        } for src in sources]
    })

