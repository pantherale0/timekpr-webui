import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from src.database import db, ManagedUser, AgentDevice, Settings, AppPolicy
from src.agent_helper import AgentConnectionManager
from src.helpers import _format_seconds, _get_device_label_map, _device_display_label
from src.alerts_manager import _build_user_alert_groups, _build_device_alert_entries
from src.apparmor_manager import _get_apparmor_usage_summary
from src.blocklists_manager import (
    _get_user_assigned_blocklist_source_ids,
    _build_user_blocklist_sync_status,
    _get_blocklist_sources,
)
from src.settings_manager import (
    _get_agent_websocket_url,
    _get_alert_webhook_settings,
    _get_time_sync_tolerance,
    _get_alert_retention_days,
    _get_android_agent_apk_filename,
    _get_android_agent_signature_checksum,
)
from src.pairing_helper import (
    build_agent_websocket_url,
    get_server_version,
    has_uploaded_android_apk,
    normalize_agent_websocket_url,
    pairing_payload_json,
    remove_uploaded_android_apk,
    render_pairing_qr_data_uri,
    resolve_android_provisioning,
    save_uploaded_android_apk,
)

_LOGGER = logging.getLogger(__name__)

ui_dashboard_bp = Blueprint('ui_dashboard', __name__)


@ui_dashboard_bp.route('/dashboard')
def dashboard():
    """Render the main dashboard with user status and recent usage data."""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))
    
    db.session.expire_all()
    users = ManagedUser.query.all()
    pending_adjustments = {}
    
    user_data = []
    for user in users:
        usage_data = user.get_recent_usage(days=7)
        mapping_count = len(user.device_mappings)
        online_mapping_count = sum(
            1 for mapping in user.device_mappings if AgentConnectionManager.is_online(mapping.system_id)
        )
        valid_mapping_count = sum(1 for mapping in user.device_mappings if mapping.is_valid)
        time_left_formatted = _format_seconds(user.get_effective_time_left_seconds())
        
        if user.pending_time_adjustment is not None and user.pending_time_operation is not None:
            minutes = user.pending_time_adjustment // 60
            operation = user.pending_time_operation
            pending_adjustments[str(user.id)] = f"{operation}{minutes} minutes"
        
        user_data.append({
            'id': user.id,
            'username': user.username,
            'is_online': online_mapping_count > 0,
            'mapping_count': mapping_count,
            'online_mapping_count': online_mapping_count,
            'valid_mapping_count': valid_mapping_count,
            'last_checked': user.last_checked,
            'usage_data': usage_data,
            'time_left': time_left_formatted,
            'weekly_schedule': user.weekly_schedule
        })

    users_sorted = sorted(user_data, key=lambda item: item['username'].lower())
    return render_template('dashboard.html', users=users_sorted, pending_adjustments=pending_adjustments)


@ui_dashboard_bp.route('/admin')
def admin():
    """Redirect to the users admin page for backwards-compatibility."""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))
    return redirect(url_for('ui_dashboard.admin_users'))


@ui_dashboard_bp.route('/admin/users')
def admin_users():
    """Render the administration page for users and mappings."""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))
    
    users = ManagedUser.query.order_by(ManagedUser.username.asc()).all()
    device_labels = _get_device_label_map()
    approved_devices = AgentDevice.query.filter_by(status='approved').all()
    return render_template(
        'admin_users.html',
        users=users,
        approved_devices=approved_devices,
        device_labels=device_labels,
    )


@ui_dashboard_bp.route('/admin/users/<int:user_id>')
def edit_user_profile(user_id):
    """Render the user profile modification dashboard."""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))
    
    user = ManagedUser.query.get_or_404(user_id)
    blocklist_sync_status = _build_user_blocklist_sync_status(user)
    blocklist_sources = _get_blocklist_sources(include_domains=False, enabled_only=True)
    app_policies = AppPolicy.query.order_by(AppPolicy.name.asc()).all()
    assigned_policy_ids = {assignment.policy_id for assignment in user.app_policy_assignments}
    
    return render_template(
        'admin_user_edit.html',
        user=user,
        blocklist_sources=blocklist_sources,
        blocklist_sync_status=blocklist_sync_status,
        app_policies=app_policies,
        assigned_policy_ids=assigned_policy_ids,
    )


@ui_dashboard_bp.route('/admin/devices')
def admin_devices():
    """Render the administration page for agent devices."""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))
    
    device_labels = _get_device_label_map()
    approved_devices = AgentDevice.query.filter_by(status='approved').all()
    pending_devices = AgentDevice.query.filter_by(status='pending').all()
    return render_template(
        'admin_devices.html',
        approved_devices=approved_devices,
        pending_devices=pending_devices,
        device_labels=device_labels,
    )


@ui_dashboard_bp.route('/admin/restrictions')
def admin_restrictions():
    """Render the dedicated Internet Restrictions page."""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))
    
    blocklist_sources = _get_blocklist_sources(include_domains=True)
    return render_template(
        'restrictions.html',
        blocklist_sources=blocklist_sources,
    )


@ui_dashboard_bp.route('/settings', methods=['GET', 'POST'])
def settings():
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))

    alert_webhook_settings = _get_alert_webhook_settings()
    time_sync_tolerance = _get_time_sync_tolerance()
    alert_retention_days = _get_alert_retention_days()
    agent_websocket_url = _get_agent_websocket_url()
    android_agent_apk_filename = _get_android_agent_apk_filename()
    android_agent_signature_checksum = _get_android_agent_signature_checksum()
    android_agent_apk_uploaded = has_uploaded_android_apk()

    if request.method == 'POST':
        form_name = (request.form.get('form_name') or 'password').strip()

        if form_name == 'agent_pairing':
            submitted_url = (request.form.get('agent_websocket_url') or '').strip()
            try:
                normalized_url = normalize_agent_websocket_url(submitted_url)
            except ValueError as exc:
                flash(str(exc), 'danger')
            else:
                Settings.set_value('agent_websocket_url', normalized_url)
                if normalized_url:
                    flash('Agent pairing URL updated successfully', 'success')
                else:
                    flash('Agent pairing URL reset to auto-detect', 'success')
                return redirect(url_for('ui_dashboard.settings'))

            agent_websocket_url = submitted_url
        elif form_name == 'android_provisioning':
            if request.form.get('remove_android_apk') == '1':
                remove_uploaded_android_apk()
                Settings.set_value('android_agent_apk_filename', '')
                Settings.set_value('android_agent_signature_checksum', '')
                flash('Uploaded Android APK removed', 'success')
                return redirect(url_for('ui_dashboard.settings'))

            uploaded_apk = request.files.get('android_agent_apk')
            if uploaded_apk and (uploaded_apk.filename or '').strip():
                try:
                    filename, checksum = save_uploaded_android_apk(uploaded_apk)
                except ValueError as exc:
                    flash(str(exc), 'danger')
                except RuntimeError as exc:
                    flash(f'Failed to process uploaded APK: {exc}', 'danger')
                else:
                    Settings.set_value('android_agent_apk_filename', filename)
                    Settings.set_value('android_agent_signature_checksum', checksum)
                    flash('Android APK uploaded successfully', 'success')
                    return redirect(url_for('ui_dashboard.settings'))
            else:
                flash('Choose an APK file to upload', 'warning')
        elif form_name == 'alert_webhook':
            webhook_enabled = request.form.get('alert_webhook_enabled') == 'on'
            webhook_url = (request.form.get('alert_webhook_url') or '').strip()
            webhook_secret = (request.form.get('alert_webhook_secret') or '').strip()

            if webhook_enabled and not webhook_url:
                flash('Webhook URL is required when alert delivery is enabled', 'danger')
            else:
                Settings.set_value('alert_webhook_enabled', '1' if webhook_enabled else '0')
                Settings.set_value('alert_webhook_url', webhook_url)
                Settings.set_value('alert_webhook_secret', webhook_secret)
                flash('Alert webhook settings updated successfully', 'success')
                return redirect(url_for('ui_dashboard.settings'))

            alert_webhook_settings = {
                'enabled': webhook_enabled,
                'url': webhook_url,
                'secret': webhook_secret,
                'is_active': webhook_enabled and bool(webhook_url),
            }
        elif form_name == 'application_settings':
            tolerance = request.form.get('time_sync_tolerance')
            retention = request.form.get('alert_retention_days')
            try:
                tolerance_val = int(tolerance)
                retention_val = int(retention)
                
                if tolerance_val < 0:
                    flash('Tolerance must be a non-negative number', 'danger')
                elif retention_val < 1:
                    flash('Retention must be at least 1 day', 'danger')
                else:
                    Settings.set_value('time_sync_tolerance', str(tolerance_val))
                    Settings.set_value('alert_retention_days', str(retention_val))
                    flash('Application settings updated successfully', 'success')
                    return redirect(url_for('ui_dashboard.settings'))
            except (TypeError, ValueError):
                flash('Invalid setting values provided', 'danger')
        else:
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            if not current_password or not new_password or not confirm_password:
                flash('All fields are required', 'danger')
            elif not Settings.check_admin_password(current_password):
                flash('Current password is incorrect', 'danger')
            elif new_password != confirm_password:
                flash('New passwords do not match', 'danger')
            elif len(new_password) < 4:
                flash('New password must be at least 4 characters long', 'danger')
            else:
                Settings.set_admin_password(new_password)
                flash('Password updated successfully', 'success')
                return redirect(url_for('ui_dashboard.settings'))

    server_url = build_agent_websocket_url(
        request,
        configured_url=agent_websocket_url,
    )
    registration_token = AgentConnectionManager.registration_token
    pairing_payload = pairing_payload_json(server_url, registration_token)
    pairing_qr_data_uri = render_pairing_qr_data_uri(pairing_payload)

    provisioning = resolve_android_provisioning(
        server_url,
        get_server_version(),
        checksum_override=android_agent_signature_checksum,
        registration_token=registration_token,
    )
    provisioning_qr_data_uri = None
    if provisioning['provisioning_ready'] and provisioning['payload_json']:
        provisioning_qr_data_uri = render_pairing_qr_data_uri(provisioning['payload_json'])

    return render_template(
        'settings.html',
        alert_webhook_settings=alert_webhook_settings,
        time_sync_tolerance=time_sync_tolerance,
        alert_retention_days=alert_retention_days,
        agent_websocket_url=agent_websocket_url,
        pairing_server_url=server_url,
        pairing_qr_data_uri=pairing_qr_data_uri,
        pairing_payload=pairing_payload,
        android_agent_apk_filename=android_agent_apk_filename,
        android_agent_apk_uploaded=android_agent_apk_uploaded,
        android_agent_signature_checksum=android_agent_signature_checksum,
        provisioning=provisioning,
        provisioning_qr_data_uri=provisioning_qr_data_uri,
        server_version=get_server_version(),
    )


@ui_dashboard_bp.route('/stats/<int:user_id>')
def user_stats(user_id):
    """Display extended usage history for a single user"""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))

    user = ManagedUser.query.get_or_404(user_id)

    daily_30   = user.get_recent_usage(days=30)
    weekly_13  = user.get_usage_weekly_grouped(weeks=13)
    monthly_12 = user.get_usage_monthly_grouped(months=12)
    all_monthly = user.get_all_usage_monthly()
    alert_search = (request.args.get('alert_search') or '').strip()
    alert_groups, alert_entries, alert_summary = _build_user_alert_groups(user, search_query=alert_search)
    device_labels = _get_device_label_map()

    return render_template('stats.html',
        user=user,
        daily_30=daily_30,
        weekly_13=weekly_13,
        monthly_12=monthly_12,
        all_monthly=all_monthly,
        alert_search=alert_search,
        alert_groups=alert_groups,
        alert_entries=alert_entries,
        alert_summary=alert_summary,
        device_labels=device_labels,
    )


@ui_dashboard_bp.route('/devices/<system_id>')
def device_detail(system_id):
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))

    device = AgentDevice.query.get_or_404(system_id)
    alert_search = (request.args.get('alert_search') or '').strip()
    alert_entries, alert_summary = _build_device_alert_entries(device, search_query=alert_search)
    device_labels = _get_device_label_map()
    mapped_accounts = sorted(
        device.user_mappings,
        key=lambda mapping: (
            mapping.managed_user.username.lower() if mapping.managed_user else '',
            mapping.linux_username.lower(),
            mapping.id,
        ),
    )
    blocklist_contributors = []
    for mapping in mapped_accounts:
        user = mapping.managed_user
        if not user:
            continue
        assigned_source_ids = _get_user_assigned_blocklist_source_ids(user)
        if not assigned_source_ids:
            continue
        status = _build_user_blocklist_sync_status(user)
        blocklist_contributors.append({
            'managed_user': user.username,
            'linux_username': mapping.linux_username,
            'linux_uid': mapping.linux_uid,
            'assigned_source_count': status['assigned_source_count'],
            'effective_domain_count': status['effective_domain_count'],
            'sync_status': next(
                (
                    item['status']
                    for item in status['mappings']
                    if item['mapping_id'] == mapping.id
                ),
                'pending',
            ),
        })

    usage_summaries = {}
    for mapping in mapped_accounts:
        usage_summaries[mapping.id] = _get_apparmor_usage_summary(mapping.id)

    return render_template(
        'device_detail.html',
        device=device,
        device_label=device_labels.get(system_id, device.display_name),
        mapped_accounts=mapped_accounts,
        blocklist_contributors=blocklist_contributors,
        alert_search=alert_search,
        alert_entries=alert_entries,
        alert_summary=alert_summary,
        usage_summaries=usage_summaries,
    )
